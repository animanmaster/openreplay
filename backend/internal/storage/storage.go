package storage

import (
	"bytes"
	"context"
	"fmt"
	"go.opentelemetry.io/otel/metric/instrument/syncfloat64"
	"log"
	config "openreplay/backend/internal/config/storage"
	"openreplay/backend/pkg/flakeid"
	"openreplay/backend/pkg/monitoring"
	"openreplay/backend/pkg/storage"
	"os"
	"strconv"
	"time"
)

type Storage struct {
	cfg        *config.Config
	s3         *storage.S3
	startBytes []byte

	totalSessions       syncfloat64.Counter
	sessionDOMSize      syncfloat64.Histogram
	sessionDevtoolsSize syncfloat64.Histogram
	readingDOMTime      syncfloat64.Histogram
	readingTime         syncfloat64.Histogram
	archivingTime       syncfloat64.Histogram
}

func New(cfg *config.Config, s3 *storage.S3, metrics *monitoring.Metrics) (*Storage, error) {
	switch {
	case cfg == nil:
		return nil, fmt.Errorf("config is empty")
	case s3 == nil:
		return nil, fmt.Errorf("s3 storage is empty")
	}
	// Create metrics
	totalSessions, err := metrics.RegisterCounter("sessions_total")
	if err != nil {
		log.Printf("can't create sessions_total metric: %s", err)
	}
	sessionDOMSize, err := metrics.RegisterHistogram("sessions_size")
	if err != nil {
		log.Printf("can't create session_size metric: %s", err)
	}
	sessionDevtoolsSize, err := metrics.RegisterHistogram("sessions_dt_size")
	if err != nil {
		log.Printf("can't create sessions_dt_size metric: %s", err)
	}
	readingTime, err := metrics.RegisterHistogram("reading_duration")
	if err != nil {
		log.Printf("can't create reading_duration metric: %s", err)
	}
	archivingTime, err := metrics.RegisterHistogram("archiving_duration")
	if err != nil {
		log.Printf("can't create archiving_duration metric: %s", err)
	}
	return &Storage{
		cfg:                 cfg,
		s3:                  s3,
		startBytes:          make([]byte, cfg.FileSplitSize),
		totalSessions:       totalSessions,
		sessionDOMSize:      sessionDOMSize,
		sessionDevtoolsSize: sessionDevtoolsSize,
		readingTime:         readingTime,
		archivingTime:       archivingTime,
	}, nil
}

func (s *Storage) UploadSessionFiles(sessID uint64) error {
	sessionDir := strconv.FormatUint(sessID, 10)
	if err := s.uploadKey(sessID, sessionDir+"/dom.mob", true, 5); err != nil {
		return err
	}
	if err := s.uploadKey(sessID, sessionDir+"/devtools.mob", false, 4); err != nil {
		return err
	}
	return nil
}

// TODO: make a bit cleaner
func (s *Storage) uploadKey(sessID uint64, key string, shouldSplit bool, retryCount int) error {
	if retryCount <= 0 {
		return nil
	}

	start := time.Now()
	file, err := os.Open(s.cfg.FSDir + "/" + key)
	if err != nil {
		return fmt.Errorf("File open error: %v; sessID: %s, part: %d, sessStart: %s\n",
			err, key, sessID%16,
			time.UnixMilli(int64(flakeid.ExtractTimestamp(sessID))),
		)
	}
	defer file.Close()

	if shouldSplit {
		nRead, err := file.Read(s.startBytes)
		if err != nil {
			log.Printf("File read error: %s; sessID: %s, part: %d, sessStart: %s",
				err,
				key,
				sessID%16,
				time.UnixMilli(int64(flakeid.ExtractTimestamp(sessID))),
			)
			time.AfterFunc(s.cfg.RetryTimeout, func() {
				s.uploadKey(sessID, key, shouldSplit, retryCount-1)
			})
			return nil
		}
		s.readingTime.Record(context.Background(), float64(time.Now().Sub(start).Milliseconds()))

		start = time.Now()
		startReader := bytes.NewBuffer(s.startBytes[:nRead])
		if err := s.s3.Upload(s.gzipFile(startReader), key+"s", "application/octet-stream", true); err != nil {
			log.Fatalf("Storage: start upload failed.  %v\n", err)
		}
		if nRead == s.cfg.FileSplitSize {
			if err := s.s3.Upload(s.gzipFile(file), key+"e", "application/octet-stream", true); err != nil {
				log.Fatalf("Storage: end upload failed. %v\n", err)
			}
		}
		s.archivingTime.Record(context.Background(), float64(time.Now().Sub(start).Milliseconds()))
	} else {
		start = time.Now()
		if err := s.s3.Upload(s.gzipFile(file), key+"s", "application/octet-stream", true); err != nil {
			log.Fatalf("Storage: end upload failed. %v\n", err)
		}
		s.archivingTime.Record(context.Background(), float64(time.Now().Sub(start).Milliseconds()))
	}

	// Save metrics
	var fileSize float64 = 0
	fileInfo, err := file.Stat()
	if err != nil {
		log.Printf("can't get file info: %s", err)
	} else {
		fileSize = float64(fileInfo.Size())
	}
	ctx, _ := context.WithTimeout(context.Background(), time.Millisecond*200)
	if shouldSplit {
		s.totalSessions.Add(ctx, 1)
		s.sessionDOMSize.Record(ctx, fileSize)
	} else {
		s.sessionDevtoolsSize.Record(ctx, fileSize)
	}

	return nil
}
