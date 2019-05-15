from social_core.backends.oauth import BaseOAuth2
from base64 import urlsafe_b64encode
from urlparse import urljoin

from assembl.lib.config import get
from assembl.lib.logging import getLogger

log = getLogger('assembl')


class DecathlonOAuth(BaseOAuth2):
    """
    Decathlon OAuth authentication backend. Authorization Code flow.
    """
    name = 'decathlon'
    BASE_AS_URL = get('SOCIAL_AUTH_DECATHLON_BASE_AS_URI')
    AUTHORIZATION_URL = urljoin(BASE_AS_URL, '/as/authorization.oauth2')
    ACCESS_TOKEN_URL = urljoin(BASE_AS_URL, '/as/token.oauth2')
    REFRESH_TOKEN_URL = urljoin(BASE_AS_URL, '/token')

    ACCESS_TOKEN_METHOD = 'POST'
    SCOPE_SEPARATOR = ' '
    ID_KEY = 'uid'
    REDIRECT_STATE = False
    STATE_PARAMETER = False

    def auth_headers(self):
        headers = {}
        headers.update({'cache-control': 'no-cache'})
        # Manually override authorization
        client_id, client_secret = self.get_key_and_secret()
        credentials = "%s:%s" % (client_id, client_secret)
        headers.update({'Authorization': 'Basic %s' % urlsafe_b64encode(credentials)})
        return headers

    def get_scope_argument(self):
        return {'scope': 'openid profile email'}

    def auth_complete_credentials(self):
        return None

    def get_user_details(self, response):
        """Return user details from Decathlon account"""
        details = {
            'email': response.get('mail'),
            'first_name': response.get('givenName'),
            'last_name': response.get('familyName'),
            'username': response.get('displayName'),
            'fullname': ' '.join((response.get('givenName'), response.get('familyName')))
        }
        return details

    def auth_complete_params(self, state=None):
        params = super(DecathlonOAuth, self).auth_complete_params(state)
        if 'client_secret' in params:
            params.pop('client_secret')
        return params

    def user_data(self, token, *args, **kwargs):
        """Loads user data from service"""
        url = urljoin(self.BASE_AS_URL, '/idp/userinfo.openid')
        auth_header = {"Authorization": "Bearer %s" % token}
        try:
            return self.get_json(url, headers=auth_header)
        except ValueError:
            return None

    def refresh_token_params(self, token, *args, **kwargs):
        return {
            'grant_type': 'refresh_token',
            'refresh_token': token
        }

    def refresh_token(self, token, *args, **kwargs):
        params = self.refresh_token_params(token, *args, **kwargs)
        url = self.refresh_token_url()
        method = self.REFRESH_TOKEN_METHOD
        key = 'params' if method == 'GET' else 'data'
        request_args = {'headers': self.auth_headers(),
                        'method': method,
                        'auth': self.auth_complete_credentials(),
                        key: params}
        request = self.request(url, **request_args)
        return self.process_refresh_token_response(request, *args, **kwargs)
