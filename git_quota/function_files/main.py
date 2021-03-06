# Copyright 2021 by Google.
# Your use of any copyrighted material and any warranties, if applicable, are subject to your agreement with Google.

from google.cloud import monitoring_v3
from googleapiclient import discovery
from google.api import metric_pb2 as ga_metric
import time
import re
import os

def quotas(event, context):

    project_id = os.environ.get("TF_VAR_PROJECT")
    project_name = f"projects/{project_id}"
    metric_name = "effective-limit-for-vpc-network-peering"  # change if needed
    service = discovery.build('compute', 'beta')

    def create_metric():
        ########## CREATE CUSTOM METRIC ##########

        client = monitoring_v3.MetricServiceClient()
        descriptor = ga_metric.MetricDescriptor()
        descriptor.type = f"custom.googleapis.com/{metric_name}"
        descriptor.metric_kind = ga_metric.MetricDescriptor.MetricKind.GAUGE
        descriptor.value_type = ga_metric.MetricDescriptor.ValueType.DOUBLE
        descriptor.description = "Effective limit for VPC peering network metric."

        descriptor = client.create_metric_descriptor(name=project_name, metric_descriptor=descriptor)
        print("Created {}.".format(descriptor.name))

    def write_data_to_metric(eff_limit,network_name):
        series = monitoring_v3.TimeSeries()
        series.metric.type = f"custom.googleapis.com/{metric_name}"
        series.resource.type = "global" 
        series.metric.labels["network_name"] = network_name
 
        now = time.time()
        seconds = int(now)
        nanos = int((now - seconds) * 10 ** 9)
        interval = monitoring_v3.TimeInterval({"end_time": {"seconds": seconds, "nanos": nanos}})
        point = monitoring_v3.Point({"interval": interval, "value": {"double_value": eff_limit}})
        series.points = [point]
        client.create_time_series(name=project_name, time_series=[series])

        print("Wrote number of vpc peered networks to metric.")


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
            write_data_to_metric(eff_limit,i)
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


    try:

        create_metric()
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

        count_effective_limit(dict)

    except Exception as e:
        raise Exception("Error occurred getting Quota data: {}".format(e))