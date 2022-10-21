#import pandas as pd
from chalicelib.utils import ch_client
from datetime import datetime, timedelta
import numpy as np
pd = None

#TODO Deal with None values

def __get_two_values(response, time_index='hh', name_index='name'):
    columns = list(response[0].keys())
    name_index_val = columns.index(name_index)
    time_index_value = columns.index(time_index)

    table = [list(r.values()) for r in response]
    table_hh1 = list()
    table_hh2 = list()
    hh_vals = list()
    names_hh1 = list()
    names_hh2 = list()
    for e in table:
        if e[time_index_value] not in hh_vals and len(hh_vals) == 2:
            break
        elif e[time_index_value] not in hh_vals:
            hh_vals.append(e[time_index_value])

        if len(hh_vals) == 1:
            table_hh1.append(e)
            if e[name_index_val] not in names_hh1:
                names_hh1.append(e[name_index_val])
        elif len(hh_vals) == 2:
            table_hh2.append(e)
            if e[name_index_val] not in names_hh2:
                names_hh2.append(e[name_index_val])
    return table_hh1, table_hh2, columns, names_hh1, names_hh2


def __handle_timestep(time_step):
    base = "{0}"
    if time_step == 'hour':
        return f"toStartOfHour({base})", 3600
    elif time_step == 'day':
        return f"toStartOfDay({base})", 24*3600
    elif time_step == 'week':
        return f"toStartOfWeek({base})", 7*24*3600
    else:
        assert type(time_step) == int, "time_step must be {'hour', 'day', 'week'} or an integer representing the time step in minutes"
        return f"toStartOfInterval({base}, INTERVAL {time_step} minute)", int(time_step)*60


def query_requests_by_period(project_id, start_time=(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                        end_time=datetime.now().strftime('%Y-%m-%d'), time_step=3600, conn=None):
    function, steps = __handle_timestep(time_step)
    query = f"""WITH
  {function.format(f"toDateTime64('{start_time}', 0)")} as start,
  {function.format(f"toDateTime64('{end_time}', 0)")} as end
SELECT T1.hh, count(T2.session_id) as sessions, avg(T2.success) as success_rate, T2.url_host as names, T2.url_path as source, avg(T2.duration) as avg_duration FROM (SELECT arrayJoin(arrayMap(x -> toDateTime(x), range(toUInt32(start), toUInt32(end), {steps}))) as hh) AS T1
    LEFT JOIN (SELECT session_id, url_host, url_path, success, message, duration, {function.format('datetime')} as dtime FROM events WHERE project_id = {project_id} AND event_type = 'REQUEST') AS T2 ON T2.dtime = T1.hh GROUP BY T1.hh, T2.url_host, T2.url_path ORDER BY T1.hh DESC;
    """
    if conn is None:
        with ch_client.ClickHouseClient(database='experimental') as conn:
            res = conn.execute(query=query)
    else:
        res = conn.execute(query=query)
    table_hh1, table_hh2, columns, this_period_hosts, last_period_hosts = __get_two_values(res, time_index='hh', name_index='source')
    del res

    new_hosts = [x for x in this_period_hosts if x not in last_period_hosts]
    common_names = [x for x in this_period_hosts if x not in new_hosts]
    table_hh1, table_hh2, this_period_hosts, last_period_hosts = np.array(table_hh1), np.array(table_hh2), np.array(this_period_hosts), np.array(last_period_hosts)

    source_idx = columns.index('source')
    duration_idx = columns.index('avg_duration')
    success_idx = columns.index('success_rate')
    delta_duration = dict()
    delta_success = dict()
    for n in common_names:
        d1_tmp = table_hh1[table_hh1[:, source_idx] == n]
        d2_tmp = table_hh2[table_hh2[:, source_idx] == n]
        delta_duration[n] = d1_tmp[:, duration_idx].mean() - d2_tmp[:, duration_idx].mean()
        delta_success[n] = d1_tmp[:, success_idx].mean() - d2_tmp[:, success_idx].mean()

    names_idx = columns.index('names')
    d1_tmp = d1_tmp[d1_tmp[:, success_idx].argsort()]
    return {'ratio': list(zip(d1_tmp[:, names_idx] + d1_tmp[:, source_idx], d1_tmp[:, success_idx])),
            'increase': sorted(delta_success.items(), key=lambda k: k[1], reverse=False),
            'new_events': new_hosts}


def query_most_errors_by_period(project_id, start_time=(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                        end_time=datetime.now().strftime('%Y-%m-%d'), time_step=3600, conn=None):
    function, steps = __handle_timestep(time_step)
    query = f"""WITH
  {function.format(f"toDateTime64('{start_time}', 0)")} as start,
  {function.format(f"toDateTime64('{end_time}', 0)")} as end
SELECT T1.hh, count(T2.session_id) as sessions, T2.name as names, groupUniqArray(T2.source) as sources FROM (SELECT arrayJoin(arrayMap(x -> toDateTime(x), range(toUInt32(start), toUInt32(end), {steps}))) as hh) AS T1
    LEFT JOIN (SELECT session_id, name, source, message, {function.format('datetime')} as dtime FROM events WHERE project_id = {project_id} AND event_type = 'ERROR') AS T2 ON T2.dtime = T1.hh GROUP BY T1.hh, T2.name ORDER BY T1.hh DESC;
    """
    if conn is None:
        with ch_client.ClickHouseClient(database='experimental') as conn:
            res = conn.execute(query=query)
    else:
        res = conn.execute(query=query)

    table_hh1, table_hh2, columns, this_period_errors, last_period_errors = __get_two_values(res, time_index='hh',
                                                                                           name_index='names')
    del res

    new_errors = [x for x in this_period_errors if x not in last_period_errors]
    common_errors = [x for x in this_period_errors if x not in new_errors]
    table_hh1, table_hh2, this_period_errors, last_period_errors = np.array(table_hh1), np.array(table_hh2), np.array(
        this_period_errors), np.array(last_period_errors)

    sessions_idx = columns.index('sessions')
    names_idx = columns.index('names')
    percentage_errors = dict()
    total = table_hh1[:, sessions_idx].sum()
    error_increase = dict()
    for n in this_period_errors:
        percentage_errors[n] = (table_hh1[table_hh1[:, names_idx] == n][:, sessions_idx].sum())/total
    for n in common_errors:
        error_increase[n] = table_hh1[table_hh1[:, names_idx] == n][:, names_idx].sum() - table_hh2[table_hh2[:, names_idx] == n][:, names_idx].sum()
    return {'ratio': sorted(percentage_errors.items(), key=lambda k: k[1], reverse=True),
            'increase': sorted(error_increase.items(), key=lambda k: k[1], reverse=True),
            'new_events': new_errors}


#TODO Update model
def query_cpu_memory_by_period(project_id, start_time=(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                        end_time=datetime.now().strftime('%Y-%m-%d'), time_step=3600, conn=None):
    function, steps = __handle_timestep(time_step)
    query = f"""WITH
  {function.format(f"toDateTime64('{start_time}', 0)")} as start,
  {function.format(f"toDateTime64('{end_time}', 0)")} as end
SELECT T1.hh, count(T2.session_id) as sessions, avg(T2.avg_cpu) as cpu_used, avg(T2.avg_used_js_heap_size) as memory_used, T2.url_host as names, groupUniqArray(T2.url_path) as sources FROM (SELECT arrayJoin(arrayMap(x -> toDateTime(x), range(toUInt32(start), toUInt32(end), {steps}))) as hh) AS T1
    LEFT JOIN (SELECT session_id, url_host, url_path, avg_used_js_heap_size, avg_cpu, {function.format('datetime')} as dtime FROM events WHERE project_id = {project_id} AND event_type = 'PERFORMANCE') AS T2 ON T2.dtime = T1.hh GROUP BY T1.hh, T2.url_host ORDER BY T1.hh DESC;"""
    if conn is None:
        with ch_client.ClickHouseClient(database='experimental') as conn:
            res = conn.execute(query=query)
    else:
        res = conn.execute(query=query)
    return res
    df = pd.DataFrame(res)
    del res
    first_ts, second_ts = df['hh'].unique()[:2]
    df1 = df[df['hh'] == first_ts]
    df2 = df[df['hh'] == second_ts]
    _tmp = df2['memory_used'].mean()
    return {'cpu_increase': df1['cpu_used'].mean() - df2['cpu_used'].mean(),
            'memory_increase': (df1['memory_used'].mean() - _tmp)/_tmp}


#TODO Update model
def query_click_rage_by_period(project_id, start_time=(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                        end_time=datetime.now().strftime('%Y-%m-%d'), time_step=3600, conn=None):
    function, steps = __handle_timestep(time_step)
    click_rage_condition = "name = 'click_rage'"
    query = f"""WITH
      {function.format(f"toDateTime64('{start_time}', 0)")} as start,
      {function.format(f"toDateTime64('{end_time}', 0)")} as end
    SELECT T1.hh, count(T2.session_id) as sessions, T2.url_host as names, groupUniqArray(T2.url_path) as sources FROM (SELECT arrayJoin(arrayMap(x -> toDateTime(x), range(toUInt32(start), toUInt32(end), {steps}))) as hh) AS T1
        LEFT JOIN (SELECT session_id, url_host, url_path, {function.format('datetime')} as dtime FROM events WHERE project_id = {project_id} AND event_type = 'ISSUE' AND {click_rage_condition}) AS T2 ON T2.dtime = T1.hh GROUP BY T1.hh, T2.url_host ORDER BY T1.hh DESC;"""
    if conn is None:
        with ch_client.ClickHouseClient(database='experimental') as conn:
            res = conn.execute(query=query)
    else:
        res = conn.execute(query=query)
    return res
    # df = pd.DataFrame(res)
    # del res
    first_ts, second_ts = set(res['hh'])[:2]
    df1 = df[df['hh'] == first_ts]
    df2 = df[df['hh'] == second_ts]

    this_period_names = df1['names'].unique()
    last_period_names = df2['names'].unique()

    common_names = list()
    new_names = list()
    for n in this_period_names:
        if n in last_period_names:
            common_names.append(n)
        else:
            new_names.append(n)

    raged_increment = dict()
    for n in common_names:
        if n is None:
            continue
        _tmp = df2['sessions'][n].sum()
        raged_increment[n] = (df1['sessions'][n].sum()-_tmp)/_tmp

    total = df1['sessions'].sum()
    return {'ratio': list(zip(df1['names'], df1['sessions']/total)),
            'increase': sorted(raged_increment.items(), key=lambda k: k[1], reverse=True),
            'new_events': new_names,
            }


def fetch_selected(selectedEvents, project_id, start_time=(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                        end_time=datetime.now().strftime('%Y-%m-%d'), time_step=3600):
    assert len(selectedEvents) > 0, """'list of selected events must be non empty. Available events are 'errors',
    'network', 'rage' and 'resources''"""
    output = {}
    with ch_client.ClickHouseClient(database='experimental') as conn:
        if 'errors' in selectedEvents:
            output['errors'] = query_most_errors_by_period(project_id, start_time, end_time, time_step, conn=conn)
        if 'network' in selectedEvents:
            output['network'] = query_requests_by_period(project_id, start_time, end_time, time_step, conn=conn)
        if 'rage' in selectedEvents:
            output['rage'] = query_click_rage_by_period(project_id, start_time, end_time, time_step, conn=conn)
        if 'resources' in selectedEvents:
            output['resources'] = query_cpu_memory_by_period(project_id, start_time, end_time, time_step, conn=conn)
    return output


if __name__ == '__main__':
    # configs
    start = '2022-04-19'
    end = '2022-04-21'
    projectId = 1307
    time_step = 'hour'

    # Errors widget
    print('Errors example')
    res = query_most_errors_by_period(projectId, start_time=start, end_time=end, time_step=time_step)
    print(res)

    # Resources widgets
    print('resources example')
    res = query_cpu_memory_by_period(projectId, start_time=start, end_time=end, time_step=time_step)

    # Network widgets
    print('Network example')
    res = query_requests_by_period(projectId, start_time=start, end_time=end, time_step=time_step)
    print(res)
