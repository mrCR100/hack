import os
import sys
import gzip
from io import BytesIO
import json
import hashlib
import shutil
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import tarfile
import urllib3
import argparse

urllib3.disable_warnings()

def create_session():
    s = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    
    # Check if proxy environment variables are set
    http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    
    if http_proxy or https_proxy:
        s.proxies = {
            'http': http_proxy,
            'https': https_proxy
        }
        print('[+] Using proxy settings from environment')
    
    return s

# Create a session for all requests
session = create_session()

parser = argparse.ArgumentParser()
parser.add_argument(
    "--platform",
    type=str,
    required=False,
    default="linux/amd64",
    help="specify architecture like linux/amd64"
)
parser.add_argument(
    "--image",
    type=str,
    required=True,
    help="specify image like hello-world"
)

args = parser.parse_args()
image_os = args.platform.split("/")[0]
image_arch = args.platform.split("/")[1]

# Look for the Docker image to download
repo = 'library'
tag = 'latest'
img_parts = args.image.split('/')
try:
    img, tag = img_parts[-1].split('@')
except ValueError:
    try:
        img, tag = img_parts[-1].split(':')
    except ValueError:
        img = img_parts[-1]
# Docker client doesn't seem to consider the first element as a potential registry unless there is a '.' or ':'
if len(img_parts) > 1 and ('.' in img_parts[0] or ':' in img_parts[0]):
    registry = img_parts[0]
    repo = '/'.join(img_parts[1:-1])
else:
    registry = 'registry-1.docker.io'
    if len(img_parts[:-1]) != 0:
        repo = '/'.join(img_parts[:-1])
    else:
        repo = 'library'
repository = '{}/{}'.format(repo, img)

# Get Docker authentication endpoint when it is required
auth_url='https://auth.docker.io/token'
reg_service='registry.docker.io'

try:
    print('[+] Connecting to registry: {}'.format(registry))
    resp = session.get('https://{}/v2/'.format(registry), verify=False, timeout=30)
    if resp.status_code == 401:
        auth_url = resp.headers['WWW-Authenticate'].split('"')[1]
        try:
            reg_service = resp.headers['WWW-Authenticate'].split('"')[3]
        except IndexError:
            reg_service = ""
except requests.exceptions.RequestException as e:
    print('[-] Connection error:', str(e))
    print('[*] Troubleshooting tips:')
    print('    1. Check your internet connection')
    print('    2. If you are behind a proxy, set HTTP_PROXY and HTTPS_PROXY environment variables')
    print('    3. Try using a VPN if the registry is blocked')
    print('    4. Verify if the registry {} is accessible from your network'.format(registry))
    exit(1)

# Get Docker token (this function is useless for unauthenticated registries like Microsoft)
def get_auth_head(auth_type):
    try:
        response = session.get('{}?service={}&scope=repository:{}:pull'.format(auth_url, reg_service, repository),
                           verify=False, timeout=30)
        access_token = response.json()['token']
        head = {'Authorization':'Bearer '+ access_token, 'Accept': auth_type}
        return head
    except requests.exceptions.RequestException as e:
        print('[-] Authentication error:', str(e))
        exit(1)

# Docker style progress bar
def progress_bar(ublob, nb_traits):
    sys.stdout.write('\r' + ublob[7:19] + ': Downloading [')
    for i in range(0, nb_traits):
        if i == nb_traits - 1:
            sys.stdout.write('>')
        else:
            sys.stdout.write('=')
    for i in range(0, 49 - nb_traits):
        sys.stdout.write(' ')
    sys.stdout.write(']')
    sys.stdout.flush()

# Fetch manifest v2 and get image layer digests
print('[+] Trying to fetch manifest for {}'.format(repository))
auth_head = get_auth_head('application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json')
try:
    resp = session.get('https://{}/v2/{}/manifests/{}'.format(registry, repository, tag), headers=auth_head, verify=False, timeout=30)
except requests.exceptions.RequestException as e:
    print('[-] Manifest fetch error:', str(e))
    exit(1)
print('[+] Response status code:', resp.status_code)
print('[+] Response headers:', resp.headers)

if resp.status_code != 200:
    print('[-] Cannot fetch manifest for {} [HTTP {}]'.format(repository, resp.status_code))
    print(resp.content)
    exit(1)

content_type = resp.headers.get('content-type', '')
print('[+] Content type:', content_type)

try:
    resp_json = resp.json()
    print('[+] Response JSON structure:')
    print(json.dumps(resp_json, indent=2))
    
    # Handle manifest list (multi-arch images)
    if 'manifests' in resp_json:
        print('[+] This is a multi-arch image. Available platforms:')
        for m in resp_json['manifests']:
            if 'platform' in m:
                print('    - {}/{} ({})'.format(
                    m['platform'].get('os', 'unknown'),
                    m['platform'].get('architecture', 'unknown'),
                    m['digest']
                ))
        
        # Try to find linux/amd64 platform first, then fall back to windows/amd64
        selected_manifest = None
        for m in resp_json['manifests']:
            platform = m.get('platform', {})
            if platform.get('os') == image_os and platform.get('architecture') == image_arch:
            # if platform.get('os') == 'linux' and platform.get('architecture') == 'arm64':
                selected_manifest = m
                break
        
        if not selected_manifest:
            for m in resp_json['manifests']:
                platform = m.get('platform', {})
                if platform.get('os') == 'windows' and platform.get('architecture') == 'amd64':
                    selected_manifest = m
                    break
        
        if not selected_manifest:
            # If no preferred platform found, use the first one
            selected_manifest = resp_json['manifests'][0]
        
        print('[+] Selected platform: {}/{}'.format(
            selected_manifest['platform'].get('os', 'unknown'),
            selected_manifest['platform'].get('architecture', 'unknown')
        ))
        
        # Fetch the specific manifest
        try:
            # Get fresh auth token for manifest
            manifest_auth_head = get_auth_head('application/vnd.docker.distribution.manifest.v2+json')
            manifest_resp = session.get(
                'https://{}/v2/{}/manifests/{}'.format(registry, repository, selected_manifest['digest']),
                headers=manifest_auth_head,  # 使用新的认证头
                verify=False,
                timeout=30
            )
            if manifest_resp.status_code != 200:
                print('[-] Failed to fetch specific manifest:', manifest_resp.status_code)
                print('[-] Response content:', manifest_resp.content)
                exit(1)
            resp_json = manifest_resp.json()
            print('[+] Successfully fetched specific manifest')
        except Exception as e:
            print('[-] Error fetching specific manifest:', e)
            exit(1)
    
    # Now we should have the actual manifest with layers
    if 'layers' not in resp_json:
        print('[-] Error: No layers found in manifest')
        print('[-] Available keys:', list(resp_json.keys()))
        exit(1)
    
    layers = resp_json['layers']
    
except KeyError as e:
    print('[-] Error: Could not find required key in response:', e)
    print('[-] Available keys:', list(resp_json.keys()))
    exit(1)
except Exception as e:
    print('[-] Unexpected error:', e)
    exit(1)

# Create tmp directory if it doesn't exist
img_dir = 'tmp'
if not os.path.exists(img_dir):
    print('[+] Creating temporary directory:', img_dir)
    os.makedirs(img_dir)

config = resp_json['config']['digest']
try:
    conf_resp = session.get('https://{}/v2/{}/blobs/{}'.format(registry, repository, config), headers=auth_head, verify=False, timeout=30)
except requests.exceptions.RequestException as e:
    print('[-] Config fetch error:', str(e))
    exit(1)
file = open('{}/{}.json'.format(img_dir, config[7:]), 'wb')
file.write(conf_resp.content)
file.close()

content = [{
    'Config': config[7:] + '.json',
    'RepoTags': [ ],
    'Layers': [ ]
    }]
if len(img_parts[:-1]) != 0:
    content[0]['RepoTags'].append('/'.join(img_parts[:-1]) + '/' + img + ':' + tag)
else:
    content[0]['RepoTags'].append(img + ':' + tag)

empty_json = '{"created":"1970-01-01T00:00:00Z","container_config":{"Hostname":"","Domainname":"","User":"","AttachStdin":false, \
    "AttachStdout":false,"AttachStderr":false,"Tty":false,"OpenStdin":false, "StdinOnce":false,"Env":null,"Cmd":null,"Image":"", \
    "Volumes":null,"WorkingDir":"","Entrypoint":null,"OnBuild":null,"Labels":null}}'

# Build layer folders
parent_id=''
for layer in layers:
    ublob = layer['digest']
    # FIXME: Creating fake layer ID. Don't know how Docker generates it
    fake_layer_id = hashlib.sha256((parent_id+'\n'+ublob+'\n').encode('utf-8')).hexdigest()
    layer_dir = img_dir + '/' + fake_layer_id
    os.mkdir(layer_dir)

    # Creating VERSION file
    file = open(layer_dir + '/VERSION', 'w')
    file.write('1.0')
    file.close()

    # Creating layer.tar file
    sys.stdout.write(ublob[7:19] + ': Downloading...')
    sys.stdout.flush()
    auth_head = get_auth_head('application/vnd.docker.distribution.manifest.v2+json') # refreshing token to avoid its expiration
    try:
        b_resp = session.get('https://{}/v2/{}/blobs/{}'.format(registry, repository, ublob), headers=auth_head, stream=True, verify=False, timeout=30)
    except requests.exceptions.RequestException as e:
        print('[-] Layer fetch error:', str(e))
        exit(1)
    if b_resp.status_code != 200: # When the layer is located at a custom URL
        try:
            b_resp = session.get(layer['urls'][0], headers=auth_head, stream=True, verify=False, timeout=30)
        except requests.exceptions.RequestException as e:
            print('[-] Layer fetch error:', str(e))
            exit(1)
        if b_resp.status_code != 200:
            print('\rERROR: Cannot download layer {} [HTTP {}]'.format(ublob[7:19], b_resp.status_code, b_resp.headers['Content-Length']))
            print(b_resp.content)
            exit(1)
    # Stream download and follow the progress
    b_resp.raise_for_status()
    unit = int(b_resp.headers['Content-Length']) / 50
    acc = 0
    nb_traits = 0
    progress_bar(ublob, nb_traits)
    with open(layer_dir + '/layer_gzip.tar', "wb") as file:
        for chunk in b_resp.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
                acc = acc + 8192
                if acc > unit:
                    nb_traits = nb_traits + 1
                    progress_bar(ublob, nb_traits)
                    acc = 0
    sys.stdout.write("\r{}: Extracting...{}".format(ublob[7:19], " "*50)) # Ugly but works everywhere
    sys.stdout.flush()
    with open(layer_dir + '/layer.tar', "wb") as file: # Decompress gzip response
        unzLayer = gzip.open(layer_dir + '/layer_gzip.tar','rb')
        shutil.copyfileobj(unzLayer, file)
        unzLayer.close()
    os.remove(layer_dir + '/layer_gzip.tar')
    print("\r{}: Pull complete [{}]".format(ublob[7:19], b_resp.headers['Content-Length']))
    content[0]['Layers'].append(fake_layer_id + '/layer.tar')

    # Creating json file
    file = open(layer_dir + '/json', 'w')
    # last layer = config manifest - history - rootfs
    if layers[-1]['digest'] == layer['digest']:
        # FIXME: json.loads() automatically converts to unicode, thus decoding values whereas Docker doesn't
        json_obj = json.loads(conf_resp.content)
        del json_obj['history']
        try:
            del json_obj['rootfs']
        except: # Because Microsoft loves case in-sensitiveness
            del json_obj['rootfS']
    else: # other layers json are empty
        json_obj = json.loads(empty_json)
    json_obj['id'] = fake_layer_id
    if parent_id:
        json_obj['parent'] = parent_id
    parent_id = json_obj['id']
    file.write(json.dumps(json_obj))
    file.close()

file = open(img_dir + '/manifest.json', 'w')
file.write(json.dumps(content))
file.close()

if len(img_parts[:-1]) != 0:
    content = { '/'.join(img_parts[:-1]) + '/' + img : { tag : fake_layer_id } }
else: # when pulling only an img (without repo and registry)
    content = { img : { tag : fake_layer_id } }
file = open(img_dir + '/repositories', 'w')
file.write(json.dumps(content))
file.close()

# Create image tar and clean tmp folder
docker_tar = repo.replace('/', '_') + '_' + img + '.tar'
sys.stdout.write("Creating archive...")
sys.stdout.flush()
tar = tarfile.open(docker_tar, "w")
tar.add(img_dir, arcname=os.path.sep)
tar.close()
shutil.rmtree(img_dir)
print('\rDocker image pulled: ' + docker_tar)
