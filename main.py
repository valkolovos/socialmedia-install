import os
import re
import sys
import time

from datetime import datetime
from threading import Lock
from uuid import uuid4

import pexpect

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app)
thread_lock = Lock()
thread = None
active_installs = {}
print('started socket io app')

def retry_command(cmd):
    retries = 0
    while retries < 5:
        print(f'Executing command "{cmd}"')
        script = pexpect.spawn(cmd, encoding='utf-8', timeout=None)
        script.logfile = sys.stdout
        script.expect(pexpect.EOF)
        script.close()
        if script.exitstatus == 0:
            return script.before
        time.sleep(3)
        retries += 1
    raise Exception(f'{cmd} exceeded retry count')

def background_thread():
    """Example of how to send server generated events to clients."""
    print('starting background thread')
    response = '/-\\|'
    pos = 0
    while True:
        socketio.sleep(2)
        socketio.emit('keepAlive',
                      {'message': 'Server generated event', 'chr': response[pos]})
        pos = pos + 1 if (pos + 1) < len(response) else 0

@socketio.event
def connect():
    @copy_current_request_context
    def ack_received_connect():
        print('connect message received')

    print('client connected')
    emit('installEvent', {'message': 'connected'}, callback=ack_received_connect)
    global thread
    with thread_lock:
        if thread is None:
            thread = socketio.start_background_task(background_thread)

@socketio.event
def connect():
    print('client connected')
    emit('installEvent', {'message': 'connected'})

@socketio.event
def disconnect():
    print('client disconnected', request)

@app.route("/")
def hello_world():
    auth_login_url = re.compile(r'https://accounts.google.com/o/oauth2/auth\?.*')
    auth = pexpect.spawn('gcloud auth login --no-launch-browser --quiet', encoding='utf-8')
    auth.logfile = sys.stdout
    auth.expect(auth_login_url)
    auth_login = auth.match[0].replace('\r', '')
    auth_id = str(uuid4())
    active_installs[auth_id] = auth
    return render_template('index.html', url=auth_login, auth_id=auth_id)

@socketio.on('submitToken')
def submitToken(data):
    try:
        print(request)
        auth = active_installs[data['auth_id']]
        auth.sendline(data['token'])
        auth.expect(pexpect.EOF)
        auth.close()
        del active_installs[data['auth_id']]
        # create project
        if data.get('project'):
            project_name = data.get('project')
        else:
            project_name = f'vincent-{int(datetime.utcnow().timestamp())}'
            emit('installEvent', {'message': f'Creating project {project_name}...'})
            retry_command(f'gcloud projects create {project_name} --name="Social Media" --quiet')
            emit('installEvent', {'message': 'Done creating project'})

        retry_command(f'gcloud config set project {project_name}')

        emit('installEvent', {'message': 'Enabling billing service...'})
        retry_command('gcloud services enable cloudbilling.googleapis.com')
        emit('installEvent', {'message': 'Done billing service'})

        emit('installEvent', {'message': 'Linking billing account to project...'})
        billing_account_re = re.compile('name = (billingAccounts/[^\r]*)')
        billing_account_response = retry_command('gcloud beta billing accounts list --format=config')
        billing_account = billing_account_re.search(billing_account_response).group(1)
        retry_command(f'gcloud beta billing projects link {project_name} --billing-account={billing_account}')
        emit('installEvent', {'message': 'Done linking billing acount'})

        emit('installEvent', {'message': 'Enabling cloudbuild and cloudtasks services...'})
        retry_command('gcloud services enable cloudbuild.googleapis.com')
        retry_command('gcloud services enable cloudtasks.googleapis.com')
        emit('installEvent', {'message': 'Done enabling cloudbuild and cloudtasks services'})

        check_for_app = pexpect.spawn('gcloud app versions list', encoding='utf-8', timeout=None)
        check_for_app.logfile = sys.stdout
        check_for_app.expect(pexpect.EOF)
        check_for_app.close()
        if check_for_app.exitstatus != 0:
            emit('installEvent', {'message': 'Creating app...'})
            retry_command('gcloud app create --region=us-west2 --quiet')
            emit('installEvent', {'message': 'Done creating app'})

        retries = 0
        while retries < 5:
            get_svc_account_response = retry_command('gcloud iam service-accounts list')
            if f'{project_name}@appspot.gserviceaccount.com' in get_svc_account_response:
                break
            time.sleep(3)
            retries += 1
        if retries == 5:
            emit('installEvent', {'message': 'Service account was never created. Failing...'})
            raise Exception('Service account was never created.')

        emit('installEvent', {'message': 'Creating and downloading service account credentials...'})
        service_account_response = retry_command(
            f'gcloud iam service-accounts keys create service-account-creds.json --iam-account={project_name}@appspot.gserviceaccount.com'
        )
        service_account_re = re.compile(r'created key \[([^]]*)\]')
        service_account_id = service_account_re.search(service_account_response).group(1)

        try:
            check_for_queues_response = retry_command('gcloud tasks queues list')
        except Exception:
            check_for_queues_response = ''
        emit('installEvent', {'message': 'Creating task queues...'})
        for queue in ['post-created','post-notify','ack-connection','request-connection','comment-created']:
            if queue not in check_for_queues_response:
                emit('installEvent', {'message': f'Creating {queue}'})
                retry_command(f'gcloud tasks queues create {queue}')
        emit('installEvent', {'message': 'Done creating task queues'})

        emit('installEvent', {'message': 'Cloning code to deploy...'})
        retry_command('rm -rf socialmedia')
        retry_command('git clone https://github.com/valkolovos/socialmedia.git')
        emit('installEvent', {'message': 'Done cloning code'})

        emit('installEvent', {'message': 'Creating datastore indexes...'})
        retry_command('gcloud datastore indexes create socialmedia/index.yaml --quiet')
        emit('installEvent', {'message': 'Done creating datastore indexes'})

        emit('installEvent', {'message': 'Deploying app...'})
        retry_command('./deploy_app.sh')
        emit('installEvent', {'message': 'Done deploying app'})

        emit('installEvent', {'message': 'Cloning frontend...'})
        retry_command('rm -rf socialmedia-frontend')
        retry_command('git clone https://github.com/valkolovos/socialmedia-frontend.git')
        emit('installEvent', {'message': 'Done cloning frontend'})

        emit('installEvent', {'message': 'Installing frontend dependencies...'})
        retry_command('./frontend_install_dependencies.sh')
        emit('installEvent', {'message': 'Done installing frontend dependencies'})

        emit('installEvent', {'message': 'Building frontend...'})
        retry_command(f'./frontend_build.sh {project_name}')
        emit('installEvent', {'message': 'Done building frontend...'})

        emit('installEvent', {'message': 'Creating frontend bucket...'})
        bucket_ls_response = retry_command(f'gsutil ls -p {project_name}')
        if f'gs://frontend-{project_name}' not in bucket_ls_response:
            retry_command(f'gsutil mb -p {project_name} -l us gs://frontend-{project_name}')
            retry_command(f'gsutil defacl ch -u AllUsers:R gs://frontend-{project_name}')
        emit('installEvent', {'message': 'Done creating frontend bucket'})

        emit('installEvent', {'message': 'Deploying frontend...'})
        retry_command(f'gsutil -m cp -r socialmedia-frontend/dist/* gs://frontend-{project_name}/')
        emit('installEvent', {'message': 'Done deploying frontend'})
        emit('installEvent', {'message': f'Access your new app at http://storage.googleapis.com/frontend-{project_name}/signup-v2.html'})

        retry_command(
            f'gcloud iam service-accounts keys delete {service_account_id} --iam-account={project_name}@appspot.gserviceaccount.com --quiet'
        )
    except Exception as e:
        emit('installEvent', {'message': 'install failed - see logs for details'})
    finally:
        emit('done', {})


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

