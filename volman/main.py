#!/usr/bin/env python
import os
import json
import hashlib
import bcrypt
import base64
from tornado import web, gen, ioloop, options, process
from tornado.log import app_log

HERE = os.path.abspath(os.path.dirname(__file__))
STREAM = process.Subprocess.STREAM

@gen.coroutine
def run_with_streams(*args, **kwargs):
    '''
    Runs a subprocess as a coroutine. Returns its exit code, its stdout string, 
    and its stderr string. args is passed whoesale as the first parameter value 
    to tornado.process.Subprocess. kwargs are expanded as keyword parameters 
    values to tornado.process.Subprocess.
    '''
    proc = process.Subprocess(args, stdout=STREAM, stderr=STREAM, **kwargs)
    stdout, stderr = yield [
        gen.Task(proc.stdout.read_until_close),
        gen.Task(proc.stderr.read_until_close)
    ]
    exit_code = yield proc.wait_for_exit(raise_error=False)
    raise gen.Return((exit_code, stdout, stderr))

def tmpnb_id_to_container_id(tmpnb_id):
    '''Converts a tmpnb ID to a docker container ID.'''
    return options.options.pool_prefix + tmpnb_id

def username_to_volume_prefix(username):
    '''Hashes a username as an identifiable volume prefix.'''
    return hashlib.sha1(username).hexdigest()

def password_to_volume_suffix(password):
    '''bcrypts a password as an unidentifiable volume suffix.'''
    salt = bcrypt.gensalt()
    password_utf = password.encode('utf-8')
    hashed = bcrypt.hashpw(password_utf, salt)
    return base64.b64encode(hashed, '-_')

def owns_volume(volume_id, password):
    '''Gets if the password "unlocks" the given volume.'''
    password_utf = password.encode('utf-8')
    prefix, suffix = volume_id.split('.')
    hashed = base64.b64decode(suffix, '-_')
    return bcrypt.hashpw(password_utf, hashed) == hashed

@gen.coroutine
def create_volume(prefix, suffix):
    '''Creates a new volume.'''
    volume_id = '%s.%s' % (prefix, suffix)
    exit_code, _, _ = yield run_with_streams('docker', 'volume', 'create', '--name', volume_id)
    raise gen.Return(exit_code == 0)

@gen.coroutine
def mount_volume(volume_id, tmpnb_id):
    '''Mounts an existing volume on the given tmpnb container.'''
    env = {
        'VOLUME' : volume_id,
        'CONTAINER': tmpnb_id_to_container_id(tmpnb_id),
        'HOSTMOUNT': options.options.host_mount
    }
    exit_code, _, _ = yield run_with_streams('./attach_work.sh', cwd=HERE, env=env)
    raise gen.Return(exit_code == 0)

@gen.coroutine
def unmount_volume(volume_id, tmpnb_id):
    '''Unmounts a volume from the tmpnb container.'''
    container_id = tmpnb_id_to_container_id(tmpnb_id)
    exit_code, _, _ = yield run_with_streams('docker-enter', container_id, 'umount', '/home/jovyan/work')
    raise gen.Return(exit_code == 0)    

@gen.coroutine
def find_volume(prefix):
    '''Gets the volume ID given its prefix, if it exists.'''
    exit_code, stdout, stderr = yield run_with_streams('docker', 'volume', 'ls')
    if exit_code != 0:
        raise web.HTTPError(500, stderr)
    # Find the prefix
    start = stdout.find(prefix)
    if start == -1:
        result = None
    else:
        # Find the end of the volume ID 
        end = stdout.find('\n', start)
        result = stdout[start:end]
    raise gen.Return(result)

@gen.coroutine
def has_mount(tmpnb_id, mount_point):
    '''Gets if the container already has a volume mounted.'''
    container_id = tmpnb_id_to_container_id(tmpnb_id)
    cmd = 'cat /proc/mounts | grep %s' % mount_point
    exit_code, _, _ = yield run_with_streams('docker', 'exec', container_id, 'sh', '-c', cmd)
    raise gen.Return(exit_code == 0)

class VolumesHander(web.RequestHandler):
    '''
    Handles requests to create new docker volumes.
    '''
    @gen.coroutine
    def post(self):
        '''
        Creates a new docker volume for the user protected by the given password
        if the proper registration key is provided. Errors if the key is 
        incorrect/missing, if the volume already exists, or if the volume cannot
        be created for any other reason.
        '''
        body = json.loads(self.request.body)
        registration_key = body['registration_key']
        username = body['username']
        password = body['password']

        required_key = options.options.registration_key
        if not required_key or required_key == registration_key:
            # Hash the username as the volume prefix
            volume_prefix = username_to_volume_prefix(username)
            exists = yield find_volume(volume_prefix)
            if exists:
                # Error if the volume already exists
                raise web.HTTPError(409, 'volume %s exists' % volume_prefix)
            volume_suffix = password_to_volume_suffix(password)
            created = yield create_volume(volume_prefix, volume_suffix)
            if not created:
                # Error if volume creation failed
                raise web.HTTPError(500, 'unable to create volume') 
        else:
            raise web.HTTPError(401, 'invalid registration key %s', registration_key)

        app_log.info('created volume prefix %s', volume_prefix)

        # All good if we get here
        self.set_status(201)
        self.finish()

class MountsHandler(web.RequestHandler):
    '''
    Mounts / unmounts docker volumes on running containers.
    '''
    @gen.coroutine
    def post(self, *args):
        '''
        Mounts the given docker volume on the given running container using 
        nsenter.
        '''
        body = json.loads(self.request.body)
        username = body['username']
        password = body['password']
        tmpnb_id = body['tmpnb_id']

        in_use = yield has_mount(tmpnb_id, '/home/jovyan/work')
        if in_use:
            # Error if there's a volume already mounted on this image
            raise web.HTTPError(409, 'volume already mounted on %s' % tmpnb_id) 

        # See if a volume exists for the username
        prefix = username_to_volume_prefix(username)
        volume_id = yield find_volume(prefix)
        if not volume_id:
            # Error if the container does not exist
            raise web.HTTPError(401, 'volume prefix %s does not exist' % prefix)

        # Check if the user owns the volume
        if not owns_volume(volume_id, password):
            # Error if the password hash is not the suffix of the volume
            raise web.HTTPError(401, 'wrong password for volume prefix %s' % prefix)

        mounted = yield mount_volume(volume_id, tmpnb_id)
        if not mounted:
            # Error if the container does not mount
            raise web.HTTPError(500, 'unable to mount volume prefix %s on %s' % (prefix, tmpnb_id))

        app_log.info('mounted volume prefix %s on %s', prefix, tmpnb_id)

        # All good if we get here
        mount_id = '%s.%s' % (volume_id, tmpnb_id)
        self.set_status(200)
        self.finish({'id' : mount_id})       

def main():
    options.define('port', default=9005,
        help="Port for the REST API"
    )
    options.define('ip', default='127.0.0.1',
        help="IP address for the REST API"
    )
    options.define('host_mount', default='',
        help='Path where the host root is mounted in this container'
    )
    options.define('pool_prefix', default='',
        help='Prefix assigned by tmpnb to its pooled containers'
    )
    options.define('registration_key', default='',
        help='Registration key required to create new volumes'
    )

    options.parse_command_line()
    opts = options.options

    # regex from docker volume create
    api_handlers = [
        (r'/api/mounts(/([a-zA-Z0-9][a-zA-Z0-9_.-])+)?', MountsHandler),
        (r'/api/volumes', VolumesHander),
    ]

    api_app = web.Application(api_handlers)
    api_app.listen(opts.port, opts.ip, xheaders=True)
    app_log.info("Listening on {}:{}".format(opts.ip, opts.port))

    ioloop.IOLoop.instance().start()

if __name__ == '__main__':
    main()