import json
import uuid
from traitlets.config.configurable import Configurable
from traitlets import Unicode
from tornado import gen, web
from tornado import ioloop
from tornado.log import app_log
from tornado.escape import url_escape
from tornado.simple_httpclient import AsyncHTTPClient
from notebook.auth.login import LoginHandler as BaseLoginHandler
from notebook.auth.logout import LogoutHandler as BaseLogoutHandler

class VolumesClient(Configurable):
    '''
    Client for the volume service. Requests that volumes be mounted / 
    unmounted from running containers.
    '''
    server_url = Unicode('', config=True,
        help='Base URL of the volumes server')

    @gen.coroutine
    def create_volume(self, username, password, registration_key):
        http_client = AsyncHTTPClient()
        response = yield http_client.fetch(self.server_url + '/api/volumes', 
            method='POST',
            headers={
               'Content-Type': 'application/json'
            },
            body=json.dumps({
                'username' : username,
                'password' : password,
                'registration_key': registration_key
            }),
            raise_error=False
        )
        raise gen.Return(response)

    @gen.coroutine
    def mount_volume(self, username, password, tmpnb_id):
        http_client = AsyncHTTPClient()
        response = yield http_client.fetch(self.server_url + '/api/mounts', 
            method='POST',
            headers={
               'Content-Type': 'application/json'
            },
            body=json.dumps({
                'username' : username,
                'password' : password,
                'tmpnb_id': tmpnb_id
            }),
            raise_error=False
        )
        raise gen.Return(response)

    @gen.coroutine
    def unmount_volume(self, mount_id):
        http_client = AsyncHTTPClient()
        response = yield http_client.fetch(self.server_url + '/api/mounts/' + mount_id, 
            method='DELETE',
            raise_error=False
        )
        raise gen.Return(response)

class LoginHandler(BaseLoginHandler):
    '''
    Renders a login page. Contacts the volume manager to attach the user's 
    permanent storage. Refuses login if the volume manager indicates an error.
    '''
    def initialize(self):
        super(LoginHandler, self).initialize()
        self.vol_client = VolumesClient(config=self.settings['config'])

    def _render(self, message=None, login_state='active', **kwargs):
        '''Render the login / registration page with extra template values.'''
        self.write(self.render_template('login_register.html',
                next=url_escape(self.get_argument('next', default=self.base_url)),
                message=message,
                login_state=login_state,
                **kwargs
        ))

    @gen.coroutine
    def login(self, username, password):
        '''
        Requests that the volume owned by the user be mounted as the root of the 
        notebook working directory in this server's container. Renders the login
        page with a human-readable error if mounting fails for any reason. Sets
        '''
        # Get current tmpnb ID from the base URL, error if one cannot be found:
        # this auth handler only works within tmpnb!
        tmpnb_id = self.base_url.split('/')[2]
        # Request mount of volume on container
        result = yield self.vol_client.mount_volume(username, password, tmpnb_id)

        if result.code >= 400:
            if result.code == 599:
                self.set_status(result.code, 'Timeout')
                msg = 'The login service is down. Try again momentarily.'
            elif result.code == 401:
                self.set_status(result.code)
                msg = 'The username / password combo you entered is invalid.'
            elif result.code == 409:
                # TODO: link to a new one, but this should never happen
                self.set_status(result.code)
                msg = 'This notebook server is already in use.'
            else:
                # Make sure to give a reason in case the error code is not
                # in httplib.responses else a KeyError occurs!
                self.set_status(result.code, 'Unknown')
                msg = 'An unknown error occurred. Try again.'
            return self._render(
                message={'error': msg},
                register_state='',
                login_state='active'
            )

        # If the volume attached or was already attached, set the session cookie
        mount = json.loads(result.body.decode('utf-8'))
        self.set_secure_cookie(self.cookie_name, mount['id'])

        # Continue to the original URL
        next_url = self.get_argument('next', default=self.base_url)
        if not next_url.startswith(self.base_url):
            # require that next_url be absolute path within our path
            next_url = self.base_url
        self.redirect(next_url)

    @gen.coroutine
    def register(self, username, password, password_check, registration_key):
        '''
        Requests a new volume for the user to be protected by the given 
        password. Returns a human-readable error string back to the client on 
        any error. Invokes the login method immediately if registration 
        succeeds.

        :param username: Username to register
        :param password: Desired password
        :param password_check: Password again
        :param registration_key: Registration key
        '''
        msg = None

        # Make sure passwords match and have a minimum length
        if password != password_check:
            msg = 'Passwords do not match'
        elif len(password) < 8:
            msg = 'Password must be at least 8 characters'
        if msg is not None:
            return self._render(
                message={'error': msg},
                register_state='active',
                login_state=''
            )

        # Request creation of volume
        result = yield self.vol_client.create_volume(username, password, 
            registration_key)

        if result.code >= 400:
            if result.code == 599:
                self.set_status(result.code, 'Timeout')
                msg = 'The registration service is down. Try again momentarily.'
            elif result.code == 401:
                self.set_status(result.code)
                msg = 'The registration key you entered is invalid.'
            elif result.code == 409:
                self.set_status(result.code)
                msg = 'An account already exists for that email.'
            else:
                # Make sure to give a reason in case the error code is not
                # in httplib.responses else a KeyError occurs!
                self.set_status(result.code, 'Unknown')
                msg = 'An unknown error occurred. Try again.'
            return self._render(
                message={'error': msg},
                register_state='active',
                login_state=''
            )

        # Immediately do login if the account registers successfully
        yield self.login(username, password)

    @gen.coroutine
    def post(self):
        '''
        Handles submission of the login form. Posts information about the 
        current notebook server and the secret volume name entered by the user
        to the dynamic volume manager.
        '''
        username = self.get_argument('username')
        password = self.get_argument('password')
        registration_key = self.get_argument('registration_key', default=None)
        password_check = self.get_argument('password_check', default=None)

        # Dispatch based on the presence of the registration field
        if registration_key is None:
            yield self.login(username, password)
        else:
            yield self.register(username, password, password_check, registration_key)

    @classmethod
    def validate_security(cls, app, ssl_options=None):
        '''Logs what we're doing. No special checks.'''
        app.log.info('Using volume manager as authentication manager')

    @classmethod
    def login_available(cls, settings):
        '''Always require login when this class is active.'''
        return True

class LogoutHandler(BaseLogoutHandler):
    '''
    Contacts the volume manager to unmount the user volume. Deletes the user 
    cookie.
    '''
    def initialize(self):
        super(LogoutHandler, self).initialize()
        self.vol_client = VolumesClient(config=self.settings['config'])

    @gen.coroutine
    @web.authenticated
    def get(self):
        '''
        Clears the login cookie, schedules shutdown of the main loop, and 
        redirects the client to the root of the domain.
        '''
        app_log.info('logout triggering scheduled shutdown')
        self.clear_login_cookie()
        # Shutdown the server after this request completes
        loop = ioloop.IOLoop.current()
        loop.add_callback(loop.stop)
        # Redirect to the root of the domain
        self.redirect('/')
