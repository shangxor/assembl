import sys

from pyramid.settings import aslist

def includeme(config):
    """ This function returns a Pyramid WSGI application."""

    config.add_route('login', '/login')
    config.add_route('contextual_login', '/{discussion_slug}/login')
    config.add_route('logout', '/logout')
    config.add_route('contextual_logout', '/{discussion_slug}/logout')
    # type in u(sername), id, email, {velruse-id-type}
    config.add_route('profile_user', '/user/{type}/{identifier}')
    config.add_route('avatar', '/user/{type}/{identifier}/avatar/{size:\d+}')
    config.add_route('register', '/register')
    config.add_route('contextual_register', '/{discussion_slug}/register')
    config.add_route('user_confirm_email', '/users/email_confirm/{ticket}')
    config.add_route('contextual_user_confirm_email', '/{discussion_slug}/users/email_confirm/{ticket}')
    #config.add_route('profile_search', '/usernames/{user_name}')  Do we want this?
    # TODO: secure next three methods to avoid spamming the user.
    config.add_route('confirm_emailid_sent', '/confirm_email_sent_id/{email_account_id:\d+}')
    config.add_route('confirm_email_sent', '/confirm_email_sent/{email}')
    config.add_route('password_change_sent', '/password_change_sent/{profile_id:\d+}')
    config.add_route('request_password_change', '/req_password_change')
    config.add_route('do_password_change', '/do_password_change/{ticket}')
    config.add_route('finish_password_change', '/finish_password_change')
    

    # determine which providers we want to configure
    settings = config.get_settings()
    providers = aslist(settings['login_providers'])
    config.add_settings(login_providers=providers)
    config.add_settings(trusted_login_providers=aslist(
        settings.get('trusted_login_providers', '')))
    if not any(providers):
        sys.stderr.write('no login providers configured, double check '
                         'your ini file and add a few')

    if 'facebook' in providers:
        config.include('velruse.providers.facebook')
        config.add_facebook_login_from_settings(prefix='facebook.')

    if 'github' in providers:
        config.include('velruse.providers.github')
        config.add_github_login_from_settings(prefix='github.')

    if 'twitter' in providers:
        config.include('velruse.providers.twitter')
        config.add_twitter_login_from_settings(prefix='twitter.')

    if 'live' in providers:
        config.include('velruse.providers.live')
        config.add_live_login_from_settings(prefix='live.')

    if 'bitbucket' in providers:
        config.include('velruse.providers.bitbucket')
        config.add_bitbucket_login_from_settings(prefix='bitbucket.')

    if 'google' in providers:
        config.include('velruse.providers.google_oauth2')
        config.add_google_oauth2_login(
            #realm=settings['google.realm'],
            consumer_key=settings['google.consumer_key'],
            consumer_secret=settings['google.consumer_secret'],
        )

    if 'openid' in providers:
        config.include('velruse.providers.openid')
        config.add_openid_login(
            realm=settings['openid.realm']
        )

    if 'yahoo' in providers:
        config.include('velruse.providers.yahoo')
        config.add_yahoo_login(
            realm=settings['yahoo.realm'],
            consumer_key=settings['yahoo.consumer_key'],
            consumer_secret=settings['yahoo.consumer_secret'],
        )
