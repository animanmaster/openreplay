import React, { useEffect, useState } from 'react';
import { useStore } from 'App/mstore';
import { useObserver } from 'mobx-react-lite';
import { Loader } from 'UI';
import FunnelIssuesListItem from '../FunnelIssuesListItem';
import SessionItem from 'App/components/shared/SessionItem/SessionItem';

interface Props {
    issueId: string;
}
function FunnelIssueDetails(props: Props) {
    const { dashboardStore, metricStore } = useStore();
    const { issueId } = props;
    const filter = useObserver(() => dashboardStore.drillDownFilter);
    const widget = useObserver(() => metricStore.instance);
    const [loading, setLoading] = useState(false);
    const [funnelIssue, setFunnelIssue] = useState<any>(null);
    const [sessions, setSessions] = useState<any>([]);

    useEffect(() => {
        setLoading(true);
        widget.fetchIssue(widget.metricId, issueId, filter).then((resp: any) => {
            setFunnelIssue(resp.issue);
            setSessions(resp.sessions);
        }).finally(() => {
            setLoading(false);
        });
    }, []);

    return (
        <Loader loading={loading}>
            {funnelIssue && <FunnelIssuesListItem
                issue={funnelIssue}
                inDetails={true}
            />}

            <div className="mt-6">
                {sessions.map((session: any) => (
                    <SessionItem key={session.id} session={session} />
                ))}
            </div>
        </Loader>
    );
}

export default FunnelIssueDetails;