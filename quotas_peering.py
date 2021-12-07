from google.cloud import monitoring_v3
from google.oauth2 import service_account
from googleapiclient import discovery
import time
import re
import os

# for multiple projects, add IAM role for perm for every project
project_id = "monitoring-dashboards-d4c6"
# project_id = "maria-wojtarkowska-lab-ff4b"
# project_id = "vpn-other-end-f43f"
# project_id = "project-acn-false-6bbd"

project_name = f"projects/{project_id}"

# ROLES FOR SA: compute.network.viewer, monitoring.viewer
SERVICE_ACCOUNT_FILE = "<PATH TO KEY>"

try:
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = SERVICE_ACCOUNT_FILE
except NameError:
    print('Variable does not exist')

SCOPES = ['https://www.googleapis.com/auth/cloud-platform']

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

service = discovery.build('compute', 'beta', credentials=credentials)


def create_client():
    try:
        client = monitoring_v3.MetricServiceClient()
        now = time.time()
        seconds = int(now)
        nanos = int((now - seconds) * 10 ** 9)
        interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": seconds, "nanos": nanos},
            "start_time": {"seconds": (seconds - 86400), "nanos": nanos},
        }
)
        return (client, interval)
    except Exception as e:
        raise Exception("Error occurred creating the client: {}".format(e))


# retrieves quota for services currently in use, otherwise returns null (assume 0 for building comparison vs limits)
def get_quota_current_usage(client, project_name, interval):
    results = client.list_time_series(request={
        "name": project_name,
        "filter": 'metric.type = "compute.googleapis.com/quota/internal_lb_forwarding_rules_per_vpc_network/usage"',
        "interval": interval,
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL
    })
    results_list = list(results)
    return (results_list)


# retrieves quota for services limits
def get_quota_current_limit(client, project_name, interval):
    results = client.list_time_series(request={
        "name": project_name,
        "filter": 'metric.type = "compute.googleapis.com/quota/internal_lb_forwarding_rules_per_vpc_network/limit"',
        "interval": interval,
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL
    })
    results_list = list(results)
    return (results_list)


# Customising view
def quota_view(results_filtered):
    quotaViewList = []
    for result in results_filtered:
        quotaViewJson = {}
        quotaViewJson.update(dict(result.resource.labels))
        quotaViewJson.update(dict(result.metric.labels))
        for val in result.points:
            quotaViewJson.update({'value': val.value.int64_value})
        quotaViewList.append(quotaViewJson)
    return (quotaViewList)


def list_networks(project_id):
    request = service.networks().list(project=project_id)
    response = request.execute()
    dict = []
    for network in response['items']:
        if 'peerings' in network:
            STATE = network['peerings'][0]['state']
            if STATE == "ACTIVE":
                NETWORK = network['name']
                ID = network['id']
                for z in range(len(network['peerings'])):
                    PROJECT = re.search("(projects)(\W*)([a-zA-Z0-9-\s._]*)",network['peerings'][z]['network']).group(3)
                    PEERING_NETWORK = re.search("(networks)(\W*)([a-zA-Z0-9-\s./_]*)",network['peerings'][z]['network']).group(3)
                    d = {'network name':NETWORK,'network id':ID, 'peering project': PROJECT, 'peering network':PEERING_NETWORK}
                    dict.append(d)
    return dict


def count_effective_limit(dict):
    maxes = {}
    for i in dict: 
        usg = [j['usage'] for j in dict if j['network name']==i['peering network'] and i['network name']==j['peering network']]
        if not usg:
            pass
        else:
            suma = usg[0] + i['usage']
            maxim = max(suma,i['limit'])
            m = {i['network name']:maxim}
            maxes.update(m)

    for i in dict:
        for j in maxes:
            if i['network name']!=j:
                minim = min(maxes.values())
    for i in maxes:
        eff_limit = max(maxes[i],minim)
        print(f'Effective limit for {i}: {eff_limit}')


def set_usage_limits(k,usg,lim):
    if not usg:
        k['usage'] = 0
    else:
        for i in usg:
            if i['network_id']==k['network id']:
                k['usage'] = i['value']
            else:
                k['usage'] = 0
    if not lim:
        k['limit'] = 75  # default value
    else:
        for i in lim:
            if i['network_id']==k['network id']:
                k['limit'] = i['value']
            else:
                k['limit'] = 75  # default value


def main(project_name):
    try:
        dict = list_networks(project_id)

        client, interval = create_client()
        current_quota_usage = get_quota_current_usage(client, project_name, interval)
        current_quota_limit = get_quota_current_limit(client, project_name, interval)

        current_quota_usage_view = quota_view(current_quota_usage)
        current_quota_limit_view = quota_view(current_quota_limit)
        
        for j in dict:
            set_usage_limits(j,current_quota_usage_view,current_quota_limit_view)

            if j['peering project']!=project_id:
                peering_project_network = list_networks(j['peering project'])
                peering_project_usage = quota_view(get_quota_current_usage(client,f"projects/{j['peering project']}",interval))
                peering_project_limit = quota_view(get_quota_current_limit(client,f"projects/{j['peering project']}",interval))
                for k in peering_project_network:
                    set_usage_limits(k,peering_project_usage,peering_project_limit)
                dict = dict + peering_project_network

        print(dict)
        print(f'\nFOR {project_id}:')
        count_effective_limit(dict)

    except Exception as e:
        raise Exception("Error occurred getting Quota data: {}".format(e))

if __name__ == '__main__':
    main(project_name)
