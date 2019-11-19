import logging
from urllib.parse import urlencode
from django.http.response import HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse
from django.conf import settings
from django.contrib import auth
from social_core.exceptions import AuthForbidden

from globus_portal_framework.exc import ExpiredGlobusToken

log = logging.getLogger(__name__)


class ExpiredTokenMiddleware(MiddlewareMixin):
    """
    Catch the globus_portal_framework.ExpiredGlobusToken exception and
    redirect them to the login page with the redirection link set to
    the path they were originally going to. Most times, the user will already
    be logged into Globus, and so this will manifest as a request that takes
    slightly longer than usual, as it does all the OAuth redirects to grab
    tokens then does the work the user originally intended.
    """

    def process_exception(self, request, exception):
        if isinstance(exception, ExpiredGlobusToken):
            log.info('Tokens expired for user {}, redirecting to login.'
                     ''.format(request.user))
            auth.logout(request)
            base_url = reverse('social:begin', kwargs={'backend': 'globus'})
            params = urlencode({'next': request.get_full_path()})
            url = '{}?{}'.format(base_url, params)
            return HttpResponseRedirect(url)


class GlobusAuthExceptionMiddleware(MiddlewareMixin):
    """
    Catch the social_core.exception.AuthForbidden exception raised in
    the globus backend. The exception is raised in two cases:
     - a user tried to log in using an identity that is not a member of an
       allowed group (SOCIAL_AUTH_GLOBUS_ALLOWED_GROUPS) specified in
       settings.py,
     - none of the user linked identities is a member of the group
     If the user has one or more identities, a redirect is returned with the
     valid identities and a user will be able to login with one of them. If
     a user has no identities, a redirect will be returned to request access
     via app.globus.org.
    """

    def process_exception(self, request, exception):
        if not isinstance(exception, AuthForbidden):
            return
        if not isinstance(exception.args, tuple):
            return
        if not isinstance(exception.args[0], dict):
            return

        kwargs = exception.args[0]
        allowed_user_member_groups = kwargs.get('allowed_user_member_groups')

        # If the user has a valid linked identity, redirect back to Globus.
        if allowed_user_member_groups:
            req_ids = [g['identity_id'] for g in allowed_user_member_groups]
            strategy = exception.backend.strategy
            strategy.session_set(
                'session_message',
                'Your current account does not have sufficient access to this '
                'resource, but one of your linked identities does. Please '
                'login with one of those identities listed below.'
                    .format([g['username']
                             for g in allowed_user_member_groups])
            )
            # strategy does not handle lists well, so we need to encode the
            # list in a string before setting the variable.
            req_ids_string = ','.join(req_ids)
            strategy.session_set('session_required_identities', req_ids_string)
            return HttpResponseRedirect(reverse('social:begin',
                                                kwargs={'backend': 'globus'}))

        """
        Redirect a user to Globus App to join a group whose members have
        access to the portal. If you would like to show an error message,e.g.
        "You have to be a member of the {group_name} group to be able to access
        the portal. <a href="{group_join_url}">Join</a> the {group_name}
        group.", change the group_join_url in the redirect below to one of you
        app paths that will show such an appropriate error message.
        """
        jurl = getattr(settings, 'SOCIAL_AUTH_GLOBUS_GROUP_JOIN_URL', None)
        if jurl:
            return HttpResponseRedirect(jurl)

        group_join_url = kwargs.get('group_join_url')
        if group_join_url:
            return HttpResponseRedirect(group_join_url)

        log.warning('Authenticating to a group failed due to group not being '
                    'visible and settings.SOCIAL_AUTH_GLOBUS_GROUP_JOIN_URL '
                    'not being set. You should reconfigure one of these.')
