import sys, os
import winrm

def start_remote_service(services, remote_host, username, password):
    try:
        connection = winrm.Session(remote_host, auth=(username, password), transport='ntlm')
        print('connection established to the ', remote_host)
        for service_name in services.split(','):
            result = connection.run_cmd(f"sc start {service_name}")
            output = result.std_out.decode()
            print(output)
            if result.status_code == 0:
                print(f'the {service_name} service are started in {remote_host}')
            else:
                print(f"{service_name} is already started in {remote_host}")
    except Exception as e:
        print(str(e))

hostname = sys.argv[1]
serviceName = sys.argv[2]
username = sys.argv[3]
password = sys.argv[4]
start_remote_service(services, host, username, password)
