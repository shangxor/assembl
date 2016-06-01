from datetime import datetime
from itertools import chain
import urllib
import hashlib
import simplejson as json
from collections import defaultdict
from enum import IntEnum
import logging
from abc import abstractmethod
from sqlalchemy.ext.hybrid import hybrid_property

from sqlalchemy import (
    Boolean,
    Column,
    String,
    ForeignKey,
    Integer,
    UnicodeText,
    DateTime,
    Time,
    Binary,
    inspect,
    desc,
    event,
    Index,
    func,
    UniqueConstraint
)
from pyramid.httpexceptions import HTTPBadRequest, HTTPUnauthorized
from sqlalchemy.orm import (
    relationship, backref, deferred)
from sqlalchemy.types import Text
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy.sql.functions import count
from pyramid.security import Everyone, Authenticated
from rdflib import URIRef
from virtuoso.vmapping import PatternIriClass
from virtuoso.alchemy import CoerceUnicode
import transaction

from assembl import locale_negotiator
from ..lib import config
from ..lib.utils import get_global_base_url
from ..lib.locale import to_posix_string
from ..lib.sqla import (
    CrudOperation, get_model_watcher, ObjectNotUniqueError)
from ..lib.sqla_types import (
    URLString, EmailString, EmailUnicode, CaseInsensitiveWord)
from . import Base, DiscussionBoundBase, PrivateObjectMixin
from ..auth import *
from assembl.lib.raven_client import capture_exception, capture_message
from .langstrings import Locale
from ..semantic.namespaces import (
    SIOC, ASSEMBL, QUADNAMES, FOAF, DCTERMS, RDF)
from ..semantic.virtuoso_mapping import (
    QuadMapPatternS, USER_SECTION, PRIVATE_USER_SECTION,
    AssemblQuadStorageManager)

log = logging.getLogger('assembl')


# None-tolerant min, max
def minN(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)

def maxN(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


class AgentProfile(Base):
    """
    An agent could be a person, group, bot or computer.
    Profiles describe agents, which have multiple accounts.
    Some agents might also be users of the platforms.
    """
    __tablename__ = "agent_profile"
    rdf_class = FOAF.Agent
    rdf_sections = (USER_SECTION,)
    # This is very hackish. We need Posts to point to accounts vs users,
    # but right now they do not know the accounts.
    agent_as_account_iri = PatternIriClass(
            QUADNAMES.agent_as_account_iri,
            get_global_base_url() + '/data/AgentAccount/%d', None,
            ('id', Integer, False))

    id = Column(Integer, primary_key=True)
    name = Column(CoerceUnicode(1024),
        info={'rdf': QuadMapPatternS(
            None, FOAF.name, sections = (PRIVATE_USER_SECTION,))})
    description = Column(UnicodeText,
        info={'rdf': QuadMapPatternS(
            None, DCTERMS.description, sections = (PRIVATE_USER_SECTION,))})
    type = Column(String(60))

    __mapper_args__ = {
        'polymorphic_identity': 'agent_profile',
        'polymorphic_on': type,
        'with_polymorphic': '*'
    }

    def __repr__(self):
        r = super(AgentProfile, self).__repr__()
        name = self.name or ""
        return r[:-1] + name.encode("ascii", "ignore") + ">"

    def get_preferred_email_account(self):
        if inspect(self).attrs.accounts.loaded_value is NO_VALUE:
            account = self.db.query(AbstractAgentAccount).filter(
                (AbstractAgentAccount.profile_id == self.id)
                & (AbstractAgentAccount.email != None)).order_by(
                AbstractAgentAccount.verified.desc(),
                AbstractAgentAccount.preferred.desc()).first()
            if account:
                return account
        elif self.accounts:
            accounts = [a for a in self.accounts if a.email]
            accounts.sort(key=lambda e: (not e.verified, not e.preferred))
            if accounts:
                return accounts[0]

    def get_preferred_email(self):
        preferred_account = self.get_preferred_email_account()
        if preferred_account is not None:
            return preferred_account.email

    def real_name(self):
        if not self.name:
            for acc in self.identity_accounts:
                name = acc.real_name()
                if name:
                    self.name = name
                    break
        return self.name

    def display_name(self):
        # TODO: Prefer types?
        if self.name:
            return self.name
        for acc in self.identity_accounts:
            if acc.username:
                return acc.display_name()
        for acc in self.accounts:
            name = acc.display_name()
            if name:
                return name

    def merge(self, other_profile):
        log.warn("Merging AgentProfiles: %d <= %d" % (self.id, other_profile.id))
        session = self.db
        assert self.id
        assert not (
            isinstance(other_profile, User) and not isinstance(self, User))
        my_accounts = {a.signature(): a for a in self.accounts}
        for other_account in other_profile.accounts[:]:
            my_account = my_accounts.get(other_account.signature())
            if my_account:
                # if chrono order of accounts corresponds to merge priority
                if my_account.prefer_newest_info_on_merge == (
                        my_account.id > other_account.id):
                    # prefer info from my_account
                    my_account.merge(other_account)
                    session.delete(other_account)
                else:
                    other_account.merge(my_account)
                    other_account.profile = self
                    session.delete(my_account)
            else:
                other_account.profile = self
        if other_profile.name and not self.name:
            self.name = other_profile.name
        for post in other_profile.posts_created[:]:
            post.creator = self
            post.creator_id = self.id
        for post in other_profile.posts_moderated[:]:
            post.moderator = self
            post.moderator_id = self.id
        for attachment in other_profile.attachments[:]:
            attachment.creator = self
        from .action import Action
        for action in session.query(Action).filter_by(
            actor_id=other_profile.id).all():
                action.actor = self
                action.actor_id = self.id
        my_status_by_discussion = {
            s.discussion_id: s for s in self.agent_status_in_discussion
        }

        with self.db.no_autoflush:
            for status in other_profile.agent_status_in_discussion[:]:
                if status.discussion_id in my_status_by_discussion:
                    my_status = my_status_by_discussion[status.discussion_id]
                    my_status.user_created_on_this_discussion |= status.\
                        user_created_on_this_discussion
                    my_status.first_visit = minN(my_status.first_visit,
                                                 status.first_visit)
                    my_status.last_visit = maxN(my_status.last_visit,
                                                status.last_visit)
                    my_status.first_subscribed = minN(
                        my_status.first_subscribed, status.first_subscribed)
                    my_status.last_unsubscribed = minN(
                        my_status.last_unsubscribed, status.last_unsubscribed)
                    status.delete()
                else:
                    status.agent_profile = self


    def has_permission(self, verb, subject):
        if self is subject.owner:
            return True

        return self.db.query(Permission).filter_by(
            actor_id=self.id,
            subject_id=subject.id,
            verb=verb,
            allow=True
        ).one()

    def avatar_url(self, size=32, app_url=None, email=None):
        default = config.get('avatar.default_image_url') or \
            (app_url and app_url+'/static/img/icon/user.png')

        offline_mode = config.get('offline_mode')
        if offline_mode == "true":
            return default

        for acc in self.identity_accounts:
            url = acc.avatar_url(size)
            if url:
                return url
        # Otherwise: Use the gravatar URL
        email = email or self.get_preferred_email()
        if not email:
            return default
        default = config.get('avatar.gravatar_default') or default
        return EmailAccount.avatar_url_for(email, size, default)

    def external_avatar_url(self):
        return "/user/id/%d/avatar/" % (self.id,)

    def get_agent_preload(self, view_def='default'):
        result = self.generic_json(view_def, user_id=self.id)
        return json.dumps(result)

    @classmethod
    def count_posts_in_discussion_all_profiles(cls, discussion):
        from .post import Post
        return dict(discussion.db.query(
            Post.creator_id, count(Post.id)).filter_by(
            discussion_id=discussion.id, hidden=False).group_by(
            Post.creator_id))

    def count_posts_in_discussion(self, discussion_id):
        from .post import Post
        return self.db.query(Post).filter_by(
            creator_id=self.id, discussion_id=discussion_id).count()

    def count_posts_in_current_discussion(self):
        "CAN ONLY BE CALLED FROM API V2"
        from ..auth.util import get_current_discussion
        discussion = get_current_discussion()
        if discussion is None:
            return None
        return self.count_posts_in_discussion(discussion.id)

    def get_status_in_discussion(self, discussion_id):
        return self.db.query(AgentStatusInDiscussion).filter_by(
            discussion_id=discussion_id, profile_id=self.id).first()

    @property
    def status_in_current_discussion(self):
        # Use from api v2
        from ..auth.util import get_current_discussion
        discussion = get_current_discussion()
        if discussion:
            return self.get_status_in_discussion(discussion.id)

    def is_visiting_discussion(self, discussion_id):
        from assembl.models.discussion import Discussion
        d = Discussion.get(discussion_id)
        self.update_agent_status_last_visit(d)

    @classmethod
    def special_quad_patterns(cls, alias_maker, discussion_id):
        return [
            QuadMapPatternS(cls.agent_as_account_iri.apply(cls.id),
                SIOC.account_of, cls.iri_class().apply(cls.id),
                name=QUADNAMES.account_of_self),
            QuadMapPatternS(cls.agent_as_account_iri.apply(cls.id),
                RDF.type, SIOC.UserAccount, name=QUADNAMES.pseudo_account_type)]

    # True iff the user visits current discussion for the first time
    @property
    def is_first_visit(self):
        status = self.status_in_current_discussion
        if status:
            return status.last_visit == status.first_visit
        return True

    @property
    def last_visit(self):
        status = self.status_in_current_discussion
        if status:
            return status.last_visit

    @property
    def first_visit(self):
        status = self.status_in_current_discussion
        if status:
            return status.first_visit

    @property
    def was_created_on_current_discussion(self):
        # Use from api v2
        status = self.status_in_current_discussion
        if status:
            return status.user_created_on_this_discussion
        return False

    def is_owner(self, user_id):
        return user_id == self.id

    def get_preferred_locale(self):
        # TODO: per-user preferred locale
        # Want a 2-letter locale string
        # Currently expecting only a scalar value, not a list. Might change
        # In the near future.
        prefs = self.language_preference
        prefs.sort()  # natural order defined on class
        if prefs is None or len(prefs) is 0:
            # Correct way is to get the default from the app global config
            prefs = config.get_config().\
                get('available_languages', 'fr_CA en_CA').split()[0]
            assert prefs[0]
            return prefs[0]
        return Locale.locale_collection_byid[prefs[0].locale_id]


class AbstractAgentAccount(Base):
    """An abstract class for accounts that identify agents"""
    __tablename__ = "abstract_agent_account"
    rdf_class = SIOC.UserAccount
    rdf_sections = (PRIVATE_USER_SECTION,)
    prefer_newest_info_on_merge = True

    id = Column(Integer, primary_key=True)

    type = Column(String(60))

    profile_id = Column(
        Integer,
        ForeignKey('agent_profile.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True,
        info={'rdf': QuadMapPatternS(None, SIOC.account_of, sections=(USER_SECTION,))})

    profile = relationship('AgentProfile', backref=backref(
        'accounts', cascade="all, delete-orphan"))

    preferred = Column(Boolean(), default=False, server_default='0')
    verified = Column(Boolean(), default=False, server_default='0')
    # Note some social accounts don't disclose email (eg twitter), so nullable
    # Virtuoso + nullable -> no unique index (sigh)
    # Also, unverified emails are allowed to collide.
    # IMPORTANT: Use email_ci below when appropriate.
    email = Column(EmailString(100))

    # Access to email as a case-insensitive object,
    # for comparison and search purposes.
    @hybrid_property
    def email_ci(self):
        return CaseInsensitiveWord(self.email)

    __table_args__ = (
        Index("ix_abstract_agent_account_email", email)  # nigh useless
        if Base.using_virtuoso else
        Index("ix_public_abstract_agent_account_email_ci", func.lower(email)),)

    # info={'rdf': QuadMapPatternS(None, SIOC.email)}
    # Note: we could also have a FOAF.mbox, but we'd have to make
    # them into URLs with mailto:

    full_name = Column(CoerceUnicode(512))

    def signature(self):
        "Identity of signature implies identity of underlying account"
        return ('abstract_agent_account', self.id)

    def merge(self, other):
        pass

    def is_owner(self, user_id):
        return self.profile_id == user_id

    @classmethod
    def restrict_to_owners(cls, query, user_id):
        "filter query according to object owners"
        return query.filter(cls.profile_id == user_id)

    __mapper_args__ = {
        'polymorphic_identity': 'abstract_agent_account',
        'polymorphic_on': type,
        'with_polymorphic': '*'
    }

    crud_permissions = CrudPermissions(
        P_READ, P_SYSADMIN, P_SYSADMIN, P_SYSADMIN,
        P_READ, P_READ, P_READ)

    @classmethod
    def user_can_cls(cls, user_id, operation, permissions):
        s = super(AbstractAgentAccount, cls).user_can_cls(
            user_id, operation, permissions)
        return IF_OWNED if s is False else s

    def user_can(self, user_id, operation, permissions):
        # bypass for permission-less new users
        if user_id == self.profile_id:
            return True
        return super(AbstractAgentAccount, self).user_can(
            user_id, operation, permissions)

    def update_from_json(
            self, json, user_id=None, context=None, jsonld=None,
            parse_def_name='default_reverse'):
        # DO NOT update email... but we still want
        # to allow to set it on create.
        if 'email' in json:
            del json['email']
        return super(AbstractAgentAccount, self).update_from_json(
            json, user_id, context, jsonld, parse_def_name)


class EmailAccount(AbstractAgentAccount):
    """An email account"""
    __mapper_args__ = {
        'polymorphic_identity': 'agent_email_account',
    }
    profile_e = relationship(AgentProfile, backref=backref('email_accounts'))

    def display_name(self):
        if self.verified:
            return self.email

    def signature(self):
        return ('agent_email_account',
                self.email.lower() if self.email else None)

    def merge(self, other):
        log.warn("Merging EmailAccounts: %d, %d" % (self.id, other.id))
        if other.verified:
            self.verified = True

    def other_account(self):
        if not self.verified:
            return self.db.query(self.__class__).filter_by(
                email_ci=self.email_ci, verified=True).first()

    def avatar_url(self, size=32, default=None):
        return self.avatar_url_for(self.email, size, default)

    def unique_query(self):
        query, _ = super(EmailAccount, self).unique_query()
        return query.filter_by(
            type=self.type, email_ci=self.email_ci, verified=True), self.verified

    @staticmethod
    def avatar_url_for(email, size=32, default=None):
        args = {'s': str(size)}
        if default:
            args['d'] = default
        return "//www.gravatar.com/avatar/%s?%s" % (
            hashlib.md5(email.lower()).hexdigest(), urllib.urlencode(args))

    @staticmethod
    def get_or_make_profile(session, email, name=None):
        emails = list(session.query(EmailAccount).filter_by(
            email_ci=email).all())
        # We do not want unverified user emails
        # This is costly. I should have proper boolean markers
        emails = [e for e in emails if e.verified or not isinstance(e.profile, User)]
        user_emails = [e for e in emails if isinstance(e.profile, User)]
        if user_emails:
            assert len(user_emails) == 1
            return user_emails[0]
        elif emails:
            # should also be 1 but less confident.
            return emails[0]
        else:
            profile = AgentProfile(name=name)
            emailAccount = EmailAccount(email=email, profile=profile)
            session.add(emailAccount)
            return emailAccount


class IdentityProvider(Base):
    """An identity provider (or sometimes a category of identity providers.)"""
    __tablename__ = "identity_provider"
    rdf_class = SIOC.Usergroup
    rdf_sections = (PRIVATE_USER_SECTION,)

    id = Column(Integer, primary_key=True)
    provider_type = Column(String(32), nullable=False)
    name = Column(String(60), nullable=False,
        info={'rdf': QuadMapPatternS(None, SIOC.name)})
    # TODO: More complicated model, where trust also depends on realm.
    trust_emails = Column(Boolean, default=True)

    @classmethod
    def get_by_type(cls, provider_type, create=True):
        db = cls.default_db()
        provider = db.query(cls).filter_by(
            provider_type=provider_type).first()
        if create and not provider:
            # TODO: Better heuristic for name
            name = provider_type.split("-")[0]
            provider = cls(
                provider_type=provider_type, name=name)
            db.add(provider)
            db.flush()
        return provider

    @classmethod
    def populate_db(cls, db=None):
        db = db or cls.default_db()
        providers = config.get("login_providers") or []
        trusted_providers = config.get("trusted_login_providers") or []
        if not isinstance(providers, list):
            providers = providers.split()
        if not isinstance(trusted_providers, list):
            trusted_providers = trusted_providers.split()
        db_providers = db.query(cls).all()
        db_providers_by_type = {
            p.provider_type: p for p in db_providers}
        for provider in providers:
            db_provider = db_providers_by_type.get(provider, None)
            if db_provider is None:
                db.add(cls(
                    name=provider, provider_type=provider,
                    trust_emails=(provider in trusted_providers)))
            else:
                db_provider.trust_emails = (provider in trusted_providers)


class AgentStatusInDiscussion(DiscussionBoundBase):
    __tablename__ = 'agent_status_in_discussion'
    __table_args__ = (
        UniqueConstraint('discussion_id', 'profile_id'), )

    id = Column(Integer, primary_key=True)
    discussion_id = Column(Integer, ForeignKey(
            "discussion.id", ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    discussion = relationship(
        "Discussion", backref=backref(
            "agent_status_in_discussion", cascade="all, delete-orphan"))
    profile_id = Column(Integer, ForeignKey(
            "agent_profile.id", ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    agent_profile = relationship(
        AgentProfile, backref=backref(
            "agent_status_in_discussion", cascade="all, delete-orphan"))
    first_visit = Column(DateTime)
    last_visit = Column(DateTime)
    first_subscribed = Column(DateTime)
    last_unsubscribed = Column(DateTime)
    user_created_on_this_discussion = Column(Boolean, server_default='0')

    def get_discussion_id(self):
        return self.discussion_id or self.discussion.id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        return (cls.id == discussion_id,)

    def is_owner(self, user_id):
        return user_id == self.profile_id

    crud_permissions = CrudPermissions(
        P_READ, P_ADMIN_DISC, P_ADMIN_DISC, P_ADMIN_DISC,
        P_READ, P_READ, P_READ)


@event.listens_for(AgentStatusInDiscussion, 'after_insert', propagate=True)
def send_user_to_socket_for_asid(mapper, connection, target):
    agent_profile = target.agent_profile
    if not target.agent_profile:
        agent_profile = AgentProfile.get(target.profile_id)
    agent_profile.send_to_changes(
        connection, CrudOperation.UPDATE, target.discussion_id)


class User(AgentProfile):
    """
    A Human user.
    """
    __tablename__ = "user"

    __mapper_args__ = {
        'polymorphic_identity': 'user'
    }

    id = Column(
        Integer,
        ForeignKey('agent_profile.id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True
    )

    preferred_email = Column(EmailUnicode(100))
    #    info={'rdf': QuadMapPatternS(None, FOAF.mbox)})
    verified = Column(Boolean(), default=False)
    password = deferred(Column(Binary(115)))
    timezone = Column(Time(True))
    last_login = Column(DateTime)
    login_failures = Column(Integer, default=0)
    creation_date = Column(DateTime, nullable=False, default=datetime.utcnow,
        info={'rdf': QuadMapPatternS(
            None, DCTERMS.created, sections=(PRIVATE_USER_SECTION,))})

    def __init__(self, **kwargs):
        if kwargs.get('password', None) is not None:
            from ..auth.password import hash_password
            kwargs['password'] = hash_password(kwargs['password'])

        super(User, self).__init__(**kwargs)

    @property
    def real_name_p(self):
        return self.real_name()

    @real_name_p.setter
    def real_name_p(self, name):
        if name:
            name = name.strip()
        if not name:
            return
        elif len(name) < 3:
            if not self.name or len(self.name) < len(name):
                self.name = name
        else:
            self.name = name

    @property
    def username_p(self):
        if self.username:
            return self.username.username

    @username_p.setter
    def username_p(self, name):
        if self.username:
            if name:
                self.username.username = name
            else:
                self.db.delete(self.username)
        elif name:
            self.username = Username(username=name)

    @username_p.deleter
    def username_p(self):
        if self.username:
            self.db.delete(self.username)

    @property
    def password_p(self):
        return ""

    @password_p.setter
    def password_p(self, password):
        from ..auth.password import hash_password
        if password:
            self.password = hash_password(password)

    def check_password(self, password):
        if self.password:
            from ..auth.password import verify_password
            return verify_password(password, self.password)
        return False

    def get_preferred_email(self):
        if self.preferred_email:
            return self.preferred_email
        return super(User, self).get_preferred_email()

    def merge(self, other_user):
        log.warn("Merging Users: %d <= %d" % (self.id, other_user.id))
        super(User, self).merge(other_user)
        if isinstance(other_user, User):
            session = self.db
            if other_user.preferred_email and not self.preferred_email:
                self.preferred_email = other_user.preferred_email
            if other_user.last_login:
                if self.last_login:
                    self.last_login = max(
                        self.last_login, other_user.last_login)
                else:
                    self.last_login = other_user.last_login
            self.creation_date = min(
                self.creation_date, other_user.creation_date)
            if other_user.password and not self.password:
                # NOTE: The user may be confused by the implicit change of
                # password when we destroy the second account.
                # Use most recent login
                if other_user.last_login and (
                        (not self.last_login)
                        or (other_user.last_login > self.last_login)):
                    self.password = other_user.password
            for extract in other_user.extracts_created[:]:
                extract.creator = self
            for extract in other_user.extracts_owned[:]:
                extract.owner = self
            for role in other_user.roles[:]:
                role.user = self
            for role in other_user.local_roles[:]:
                role.user = self
            for announcement in other_user.announcements_created[:]:
                announcement.creator = self
            for announcement in other_user.announcements_updated[:]:
                announcement.last_updated_by = self
            if other_user.username and not self.username:
                self.username = other_user.username
                other_user.username = None
            my_lang_pref_signatures = {
                (lp.locale_id, lp.source_of_evidence)
                for lp in self.language_preference
            }
            for lang_pref in other_user.language_preference:
                if ((lang_pref.locale_id, lang_pref.source_of_evidence) in
                        my_lang_pref_signatures):
                    # First rough implementation: One has priority.
                    # There is no internal merging that makes sense,
                    # except maybe reordering (punted)
                    lang_pref.delete()
                else:
                    lang_pref.user_id = self.id
                    # TODO: Ensure consistent order value.
            old_autoflush = session.autoflush
            session.autoflush = False
            for notification_subscription in \
                    other_user.notification_subscriptions[:]:
                notification_subscription.user = self
                notification_subscription.user_id = self.id
                if notification_subscription.find_duplicate(False) is not None:
                    self.db.delete(notification_subscription)
            session.autoflush = old_autoflush

    def send_email(self, **kwargs):
        subject = kwargs.get('subject', '')
        body = kwargs.get('body', '')

        # Send email.

    def avatar_url(self, size=32, app_url=None, email=None):
        return super(User, self).avatar_url(
            size, app_url, email or self.preferred_email)

    def display_name(self):
        if self.name:
            return self.name
        if self.username:
            return self.username.username
        return super(User, self).display_name()

    @property
    def permissions_for_current_discussion(self):
        from ..auth.util import get_current_discussion
        discussion = get_current_discussion()
        if discussion:
            return {discussion.uri(): self.get_permissions(discussion.id)}
        return self.get_all_permissions()

    def get_permissions(self, discussion_id):
        from ..auth.util import get_permissions
        return get_permissions(self.id, discussion_id)

    def get_all_permissions(self):
        from ..auth.util import get_permissions
        from .discussion import Discussion
        permissions = {
            Discussion.uri_generic(d_id): get_permissions(self.id, d_id)
            for (d_id,) in self.db.query(Discussion.id)}
        return permissions

    def send_to_changes(self, connection=None, operation=CrudOperation.UPDATE,
                        discussion_id=None, view_def="changes"):
        super(User, self).send_to_changes(
            connection, operation, discussion_id, view_def)
        watcher = get_model_watcher()
        if operation == CrudOperation.UPDATE:
            watcher.processAccountModified(self.id)
        elif operation == CrudOperation.CREATE:
            watcher.processAccountCreated(self.id)

    def has_role_in(self, discussion, role):
        return self.db.query(LocalUserRole).join(Role).filter(
            LocalUserRole.user_id == self.id,
            Role.name == role,
            LocalUserRole.discussion_id == discussion.id).first()

    def create_agent_status_in_discussion(self, discussion):
        s = self.get_status_in_discussion(discussion.id)
        if s:
            return s

        s = AgentStatusInDiscussion(
            agent_profile=self,
            discussion=discussion)

        self.db.add(s)
        return s

    def update_agent_status_last_visit(self, discussion, status=None):
        agent_status = status or self.create_agent_status_in_discussion(discussion)
        _now = datetime.utcnow()
        agent_status.last_visit = _now
        if not agent_status.first_visit:
            agent_status.first_visit = _now

    def update_agent_status_subscribe(self, discussion):
        # Set the AgentStatusInDiscussion
        agent_status = self.create_agent_status_in_discussion(discussion)
        if not agent_status.first_subscribed:
            _now = datetime.utcnow()
            agent_status.first_subscribed = _now

    def update_agent_status_unsubscribe(self, discussion):
        agent_status = self.create_agent_status_in_discussion(discussion)
        _now = datetime.utcnow()
        agent_status.last_unsubscribed = _now

    def subscribe(self, discussion, role=R_PARTICIPANT):
        if not self.has_role_in(discussion, role):
            role = self.db.query(Role).filter_by(name=role).one()
            self.db.add(LocalUserRole(
                user=self, role=role, discussion=discussion))
        # Set the AgentStatusInDiscussion
        self.update_agent_status_subscribe(discussion)

    def unsubscribe(self, discussion, role=R_PARTICIPANT):
        lur = self.has_role_in(discussion, role)
        if lur:
            self.db.delete(lur)
        # Set the AgentStatusInDiscussion
        self.update_agent_status_unsubscribe(discussion)

    @classmethod
    def extra_collections(cls):
        from assembl.views.traversal import (
            CollectionDefinition, AbstractCollectionDefinition,
            UserNSBoundDictContext)
        from .notification import NotificationSubscription
        from .discussion import Discussion
        from .user_key_values import UserPreferenceCollection
        class NotificationSubscriptionCollection(CollectionDefinition):
            def __init__(self, cls):
                super(NotificationSubscriptionCollection, self).__init__(
                    cls, User.notification_subscriptions.property)

            def decorate_query(self, query, owner_alias, last_alias, parent_instance, ctx):

                query = super(
                    NotificationSubscriptionCollection, self).decorate_query(
                    query, owner_alias, last_alias, parent_instance, ctx)
                discussion = ctx.get_instance_of_class(Discussion)
                if discussion is not None:
                    # Materialize active subscriptions... TODO: Make this batch,
                    # also dematerialize
                    if isinstance(parent_instance, UserTemplate):
                        parent_instance.get_notification_subscriptions()
                    else:
                        parent_instance.get_notification_subscriptions(discussion.id)
                    query = query.filter(last_alias.discussion_id == discussion.id)
                return query

            def decorate_instance(
                    self, instance, parent_instance, assocs, user_id,
                    ctx, kwargs):
                super(NotificationSubscriptionCollection,
                      self).decorate_instance(
                      instance, parent_instance, assocs, user_id, ctx, kwargs)

            def contains(self, parent_instance, instance):
                if not super(NotificationSubscriptionCollection, self).contains(
                        parent_instance, instance):
                    return False
                # Don't I need the context to get the discussion? Rats!
                return True

            def get_default_view(self):
                return "extended"

        class LocalRoleCollection(CollectionDefinition):
            def __init__(self, cls):
                super(LocalRoleCollection, self).__init__(
                    cls, User.local_roles.property)

            def decorate_query(self, query, owner_alias, last_alias, parent_instance, ctx):

                query = super(
                    LocalRoleCollection, self).decorate_query(
                    query, owner_alias, last_alias, parent_instance, ctx)
                discussion = ctx.get_instance_of_class(Discussion)
                if discussion is not None:
                    query = query.filter(last_alias.discussion_id == discussion.id)
                return query

            def decorate_instance(
                    self, instance, parent_instance, assocs, user_id,
                    ctx, kwargs):
                super(LocalRoleCollection,
                      self).decorate_instance(
                      instance, parent_instance, assocs, user_id, ctx, kwargs)

            def contains(self, parent_instance, instance):
                if not super(LocalRoleCollection, self).contains(
                        parent_instance, instance):
                    return False
                # Don't I need the context to get the discussion? Rats!
                return True

            def get_default_view(self):
                return "default"

        class PreferencePseudoCollection(AbstractCollectionDefinition):
            def __init__(self):
                super(PreferencePseudoCollection, self).__init__(
                    cls, UserPreferenceCollection)

            def decorate_query(
                    self, query, owner_alias, coll_alias, parent_instance,
                    ctx):
                log.error("This should not happen")

            def decorate_instance(
                    self, instance, parent_instance, assocs, user_id, ctx,
                    kwargs):
                log.error("This should not happen")

            def contains(self, parent_instance, instance):
                log.error("This should not happen")

            def make_context(self, parent_ctx):
                from ..auth.util import (
                    get_current_user_id, user_has_permission)
                user_id = parent_ctx._instance.id
                discussion = None
                discussion_id = parent_ctx.get_discussion_id()
                current_user_id = get_current_user_id()
                if user_id != current_user_id and not user_has_permission(
                        discussion_id, current_user_id, P_SYSADMIN):
                    raise HTTPUnauthorized()
                if discussion_id:
                    discussion = Discussion.get(discussion_id)
                coll = UserPreferenceCollection(user_id, discussion)
                return UserNSBoundDictContext(coll, parent_ctx)

        return {
            'notification_subscriptions': NotificationSubscriptionCollection(cls),
            'local_roles': LocalRoleCollection(cls),
            'preferences': PreferencePseudoCollection()}

    def get_notification_subscriptions_for_current_discussion(self):
        "CAN ONLY BE CALLED FROM API V2"
        from ..auth.util import get_current_discussion
        discussion = get_current_discussion()
        if discussion is None:
            return []
        return self.get_notification_subscriptions(discussion.id)

    def get_preferences_for_discussion(self, discussion):
        from .user_key_values import UserPreferenceCollection
        return UserPreferenceCollection(self.id, discussion)

    def get_preferences_for_current_discussion(self):
        from ..auth.util import get_current_discussion
        discussion = get_current_discussion()
        if discussion:
            return self.get_preferences_for_discussion(discussion)

    def get_notification_subscriptions(
            self, discussion_id, reset_defaults=False):
        """the notification subscriptions for this user and discussion.
        Includes materialized subscriptions from the template."""
        from .notification import (
            NotificationSubscription, NotificationSubscriptionStatus, NotificationCreationOrigin)
        from .discussion import Discussion
        from ..auth.util import get_roles
        my_subscriptions = self.db.query(NotificationSubscription).filter_by(
            discussion_id=discussion_id, user_id=self.id).all()
        my_subscriptions_classes = {s.__class__ for s in my_subscriptions}
        needed_classes = UserTemplate.get_applicable_notification_subscriptions_classes()
        missing = set(needed_classes) - my_subscriptions_classes
        if (not missing) and not reset_defaults:
            return my_subscriptions
        discussion = Discussion.get(discussion_id)
        assert discussion
        my_roles = get_roles(self.id, discussion_id)
        subscribed = defaultdict(bool)
        for role in my_roles:
            template, changed = discussion.get_user_template(
                role, role == R_PARTICIPANT)
            if template is None:
                continue
            template_subscriptions = template.get_notification_subscriptions()
            for subscription in template_subscriptions:
                subscribed[subscription.__class__] |= subscription.status == NotificationSubscriptionStatus.ACTIVE
        if reset_defaults:
            for sub in my_subscriptions[:]:
                if (sub.creation_origin ==
                        NotificationCreationOrigin.DISCUSSION_DEFAULT
                        # only actual defaults
                        and sub.__class__ in subscribed):
                    if (sub.status == NotificationSubscriptionStatus.ACTIVE
                            and not subscribed[sub.__class__]):
                        sub.status = NotificationSubscriptionStatus.INACTIVE_DFT
                    elif (sub.status == NotificationSubscriptionStatus.INACTIVE_DFT
                            and subscribed[sub.__class__]):
                        sub.status = NotificationSubscriptionStatus.ACTIVE
            missing = set(needed_classes) - my_subscriptions_classes
        defaults = []
        for cls in missing:
            active = subscribed[cls]
            sub = cls(
                discussion_id=discussion_id,
                user_id=self.id,
                creation_origin=NotificationCreationOrigin.DISCUSSION_DEFAULT,
                status=NotificationSubscriptionStatus.ACTIVE if active else NotificationSubscriptionStatus.INACTIVE_DFT)
            defaults.append(sub)
            if active:
                # materialize
                self.db.add(sub)
        self.db.flush()
        return chain(my_subscriptions, defaults)

    def user_can(self, user_id, operation, permissions):
        # bypass for permission-less new users
        if user_id == self.id:
            return True
        return super(User, self).user_can(user_id, operation, permissions)



class Username(Base):
    """Optional usernames for users
    This is in one-one relationships to users.
    Usernames are unique, and in one-one relationships to users.
    It exists because we cannot have a unique index on a nullable property in virtuoso.
    """
    __tablename__ = 'username'
    user_id = Column(Integer,
                     ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'),
                     nullable=False, unique=True, index=True)
    username = Column(CoerceUnicode(20), primary_key=True)
    user = relationship(User, backref=backref('username', uselist=False, lazy="joined"))

    def get_id_as_str(self):
        return str(self.user_id)

    # @classmethod
    # def special_quad_patterns(cls, alias_maker, discussion_id):
    #     return [QuadMapPatternS(User.iri_class().apply(Username.user_id),
    #         SIOC.name, Username.username,
    #         name=QUADNAMES.class_User_username, sections=(PRIVATE_USER_SECTION,))]


class Role(Base):
    """A role that a user may have in a discussion"""
    __tablename__ = 'role'

    id = Column(Integer, primary_key=True)
    name = Column(String(20), nullable=False)

    @classmethod
    def get_role(cls, name, session=None):
        session = session or cls.default_db()
        return session.query(cls).filter_by(name=name).first()

    @classmethod
    def populate_db(cls, db=None):
        db = db or cls.default_db()
        roles = {r[0] for r in db.query(cls.name).all()}
        for role in SYSTEM_ROLES - roles:
            db.add(cls(name=role))


class UserRole(Base, PrivateObjectMixin):
    """roles that a user has globally (eg admin.)"""
    __tablename__ = 'user_role'
    rdf_sections = (USER_SECTION,)
    rdf_class = SIOC.Role

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True, info={'rdf': QuadMapPatternS(
            None, SIOC.function_of,
            AgentProfile.agent_as_account_iri.apply(None))})
    user = relationship(
        User, backref=backref("roles", cascade="all, delete-orphan"))
    role_id = Column(
        Integer, ForeignKey('role.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    role = relationship(Role, lazy="joined")

    def get_user_uri(self):
        return User.uri_generic(self.user_id)

    def get_role_name(self):
        return self.role.name

    @classmethod
    def special_quad_patterns(cls, alias_maker, discussion_id):
        role_alias = alias_maker.alias_from_relns(cls.role)
        return [
            QuadMapPatternS(cls.iri_class().apply(cls.id),
                SIOC.name, role_alias.name,
                name=QUADNAMES.class_UserRole_rolename,
                sections=(USER_SECTION,)),
            QuadMapPatternS(AgentProfile.agent_as_account_iri.apply(cls.user_id),
                SIOC.has_function, cls.iri_class().apply(cls.id),
                name=QUADNAMES.class_UserRole_global, sections=(USER_SECTION,)),
            # Note: The IRIs need to distinguish UserRole from LocalUserRole
            QuadMapPatternS(cls.iri_class().apply(cls.id),
                SIOC.has_scope, URIRef(AssemblQuadStorageManager.local_uri()),
                name=QUADNAMES.class_UserRole_globalscope, sections=(USER_SECTION,)),
            ]


@event.listens_for(UserRole, 'after_insert', propagate=True)
@event.listens_for(UserRole, 'after_delete', propagate=True)
def send_user_to_socket_for_user_role(mapper, connection, target):
    user = target.user
    if not target.user:
        user = User.get(target.user_id)
    user.send_to_changes(connection, CrudOperation.UPDATE, view_def="private")
    user.send_to_changes(connection, CrudOperation.UPDATE)



class LocalUserRole(DiscussionBoundBase, PrivateObjectMixin):
    """The role that a user has in the context of a discussion"""
    __tablename__ = 'local_user_role'
    rdf_sections = (USER_SECTION,)
    rdf_class = SIOC.Role

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True,
        info={'rdf': QuadMapPatternS(
            None, SIOC.function_of, AgentProfile.agent_as_account_iri.apply(None))})
    user = relationship(User, backref=backref("local_roles", cascade="all, delete-orphan"))
    discussion_id = Column(Integer, ForeignKey(
        'discussion.id', ondelete='CASCADE'), nullable=False, index=True,
        info={'rdf': QuadMapPatternS(None, SIOC.has_scope)})
    discussion = relationship(
        'Discussion', backref=backref(
            "local_user_roles", cascade="all, delete-orphan"),
        info={'rdf': QuadMapPatternS(None, ASSEMBL.in_conversation)})
    role_id = Column(Integer, ForeignKey(
        'role.id', ondelete='CASCADE', onupdate='CASCADE'),
        index=True, nullable=False)
    role = relationship(Role, lazy="joined")
    requested = Column(Boolean, server_default='0', default=False)
    # BUG in virtuoso: It will often refuse to create an index
    # whose name exists in another schema. So having this index in
    # schemas assembl and assembl_test always fails.
    # TODO: Bug virtuoso about this,
    # or introduce the schema name in the index name as workaround.
    # __table_args__ = (
    #     Index('user_discussion_idx', 'user_id', 'discussion_id'),)

    def get_discussion_id(self):
        return self.discussion_id or self.discussion.id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        return (cls.id == discussion_id,)

    def get_role_name(self):
        return self.role.name

    def unique_query(self):
        query, _ = super(LocalUserRole, self).unique_query()
        user_id = self.user_id or self.user.id
        role_id = self.role_id or self.role.id
        return query.filter_by(
            user_id=user_id, role_id=role_id), True

    def get_user_uri(self):
        return User.uri_generic(self.user_id)

    def _do_update_from_json(
            self, json, parse_def, aliases, ctx, permissions,
            user_id, duplicate_handling=None, jsonld=None):
        json_user_id = json.get('user', None)
        if json_user_id is None:
            json_user_id = user_id
        else:
            json_user_id = User.get_database_id(json_user_id)
            # Do not allow changing user
            if self.user_id is not None and json_user_id != self.user_id:
                raise HTTPBadRequest()
        self.user_id = json_user_id
        role_name = json.get("role", None)
        if not (role_name or self.role_id):
            role_name = R_PARTICIPANT
        if role_name:
            role = self.db.query(Role).filter_by(name=role_name).first()
            if not role:
                raise HTTPBadRequest("Invalid role name:"+role_name)
            self.role = role
        json_discussion_id = json.get('discussion', None)
        if json_discussion_id:
            from .discussion import Discussion
            json_discussion_id = Discussion.get_database_id(json_discussion_id)
            # Do not allow change of discussion
            if self.discussion_id is not None \
                    and json_discussion_id != self.discussion_id:
                raise HTTPBadRequest()
            self.discussion_id = json_discussion_id
        else:
            if not self.discussion_id:
                raise HTTPBadRequest()
        return self.handle_duplication(
            json, parse_def, aliases, ctx, permissions, user_id,
            duplicate_handling, jsonld)

    def is_owner(self, user_id):
        return self.user_id == user_id

    @classmethod
    def restrict_to_owners(cls, query, user_id):
        "filter query according to object owners"
        return query.filter(cls.user_id == user_id)

    @classmethod
    def base_conditions(cls, alias=None, alias_maker=None):
        cls = alias or cls
        return (cls.requested == False,)

    @classmethod
    def special_quad_patterns(cls, alias_maker, discussion_id):
        role_alias = alias_maker.alias_from_relns(cls.role)
        return [
            QuadMapPatternS(AgentProfile.agent_as_account_iri.apply(cls.user_id),
                SIOC.has_function, cls.iri_class().apply(cls.id),
                name=QUADNAMES.class_LocalUserRole_global,
                conditions=(cls.requested == False,),
                sections=(USER_SECTION,)),
            QuadMapPatternS(cls.iri_class().apply(cls.id),
                SIOC.name, role_alias.name,
                conditions=(cls.requested == False,),
                sections=(USER_SECTION,),
                name=QUADNAMES.class_LocalUserRole_rolename)]

    crud_permissions = CrudPermissions(
        P_SELF_REGISTER, P_READ, P_ADMIN_DISC, P_ADMIN_DISC,
        P_SELF_REGISTER, P_SELF_REGISTER, P_READ)

    @classmethod
    def user_can_cls(cls, user_id, operation, permissions):
        # bypass... more checks are required upstream,
        # see assembl.views.api2.auth.add_local_role
        if operation == CrudPermissions.CREATE \
                and P_SELF_REGISTER_REQUEST in permissions:
            return True
        return super(LocalUserRole, cls).user_can_cls(
            user_id, operation, permissions)


@event.listens_for(LocalUserRole, 'after_delete', propagate=True)
@event.listens_for(LocalUserRole, 'after_insert', propagate=True)
def send_user_to_socket_for_local_user_role(
        mapper, connection, target):
    user = target.user
    if not target.user:
        user = User.get(target.user_id)
    user.send_to_changes(connection, CrudOperation.UPDATE, target.discussion_id)
    user.send_to_changes(
        connection, CrudOperation.UPDATE, target.discussion_id, "private")


class Permission(Base):
    """A permission that a user may have"""
    __tablename__ = 'permission'
    id = Column(Integer, primary_key=True)
    name = Column(String(20), nullable=False)

    @classmethod
    def populate_db(cls, db=None):
        db = db or cls.default_db()
        perms = {p[0] for p in db.query(cls.name).all()}
        for perm in ASSEMBL_PERMISSIONS - perms:
            db.add(cls(name=perm))


class DiscussionPermission(DiscussionBoundBase):
    """Which permissions are given to which roles for a given discussion."""
    __tablename__ = 'discussion_permission'
    id = Column(Integer, primary_key=True)
    discussion_id = Column(Integer, ForeignKey(
        'discussion.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    discussion = relationship(
        'Discussion', backref=backref(
            "acls", cascade="all, delete-orphan"),
        info={'rdf': QuadMapPatternS(None, ASSEMBL.in_conversation)})
    role_id = Column(Integer, ForeignKey(
        'role.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    role = relationship(Role, lazy="joined")
    permission_id = Column(Integer, ForeignKey(
        'permission.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    permission = relationship(Permission, lazy="joined")

    def role_name(self):
        return self.role.name

    def permission_name(self):
        return self.permission.name

    def get_discussion_id(self):
        return self.discussion_id or self.discussion.id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        return (cls.id == discussion_id, )


def create_default_permissions(session, discussion):
    permissions = {p.name: p for p in session.query(Permission).all()}
    roles = {r.name: r for r in session.query(Role).all()}

    def add_perm(permission_name, role_names):
        # Note: Must be called within transaction manager
        for role in role_names:
            session.add(DiscussionPermission(
                discussion=discussion, role=roles[role],
                permission=permissions[permission_name]))
    add_perm(P_READ, [Everyone])
    add_perm(P_SELF_REGISTER, [Authenticated])
    add_perm(P_ADD_POST,
             [R_PARTICIPANT, R_CATCHER, R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_EDIT_POST, [R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_ADD_EXTRACT,
             [R_CATCHER, R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_EDIT_EXTRACT, [R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_EDIT_MY_EXTRACT, [R_CATCHER, R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_ADD_IDEA, [R_CATCHER, R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_EDIT_IDEA, [R_CATCHER, R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_EDIT_SYNTHESIS, [R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_SEND_SYNTHESIS, [R_MODERATOR, R_ADMINISTRATOR])
    add_perm(P_ADMIN_DISC, [R_ADMINISTRATOR])
    add_perm(P_SYSADMIN, [R_ADMINISTRATOR])
    add_perm(P_MODERATE, [R_MODERATOR, R_ADMINISTRATOR])


class AnonymousUser(DiscussionBoundBase, User):
    "A fake anonymous user bound to a source."
    __tablename__ = "anonymous_user"

    __mapper_args__ = {
        'polymorphic_identity': 'anonymous_user'
    }

    id = Column(
        Integer,
        ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True
    )

    source_id = Column(Integer, ForeignKey(
        "content_source.id",
        ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, unique=True)
    source = relationship(
        "ContentSource", backref=backref(
            "anonymous_user", cascade="all, delete-orphan",
            uselist=False))

    def __init__(self, **kwargs):
        kwargs['verified'] = True
        kwargs['name'] = "anonymous"
        super(AnonymousUser, self).__init__(**kwargs)

    # Create an index for (discussion, role)?

    def get_discussion_id(self):
        source = self.source or ContentSource.get(self.source_id)
        return source.discussion_id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        from .generic import ContentSource
        if alias_maker is None:
            anonymous_user = cls
            source = ContentSource
        else:
            anonymous_user = alias_maker.alias_from_class(cls)
            source = alias_maker.alias_from_relns(anonymous_user.source)
        return (anonymous_user.source_id == source.id,
                source.discussion_id == discussion_id)


class UserTemplate(DiscussionBoundBase, User):
    "A fake user with default permissions and Notification Subscriptions."
    __tablename__ = "user_template"

    __mapper_args__ = {
        'polymorphic_identity': 'user_template'
    }

    id = Column(
        Integer,
        ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True
    )

    discussion_id = Column(Integer, ForeignKey(
        "discussion.id", ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    discussion = relationship(
        "Discussion", backref=backref(
            "user_templates", cascade="all, delete-orphan"),
        info={'rdf': QuadMapPatternS(None, ASSEMBL.in_conversation)})

    role_id = Column(Integer, ForeignKey(
        Role.id, ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)
    for_role = relationship(Role)

    # Create an index for (discussion, role)?

    def get_discussion_id(self):
        return self.discussion_id or self.discussion.id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        return (cls.discussion_id == discussion_id,)

    @classmethod
    def get_applicable_notification_subscriptions_classes(cls):
        """
        The classes of notifications subscriptions that make sense to put in
        a template user.

        Right now, that is all concrete classes that are global to the discussion.
        """
        from ..lib.utils import get_concrete_subclasses_recursive
        from ..models import NotificationSubscriptionGlobal
        return get_concrete_subclasses_recursive(NotificationSubscriptionGlobal)

    def get_notification_subscriptions(self):
        return self.get_notification_subscriptions_and_changed()[0]

    def get_notification_subscriptions_and_changed(self):
        """the notification subscriptions for this template.
        Materializes applicable subscriptions.."""
        from .notification import (
            NotificationSubscription,
            NotificationSubscriptionStatus,
            NotificationCreationOrigin)
        # self.id may not be defined
        self.db.flush()
        needed_classes = \
            self.get_applicable_notification_subscriptions_classes()
        # We need to materialize missing NotificationSubscriptions,
        # But have duplication issues, probably due to calls on multiple
        # threads. So iterate until it works.
        query = self.db.query(NotificationSubscription).filter_by(
            discussion_id=self.discussion_id, user_id=self.id)
        changed = False
        discussion_id = self.discussion_id
        my_id = self.id
        role_name = self.for_role.name.split(':')[-1]
        # TODO: Fill from config.
        subscribed = defaultdict(bool)
        default_config = config.get_config().get(
            ".".join(("subscriptions", role_name, "default")),
            "FOLLOW_SYNTHESES")
        for role in default_config.split('\n'):
            subscribed[role.strip()] = True
        while True:
            my_subscriptions = query.all()
            my_subscriptions_classes = {s.__class__ for s in my_subscriptions}
            by_class = {cl: [sub for sub in my_subscriptions if sub.__class__ == cl]
                        for cl in my_subscriptions_classes}
            for cl, subs in by_class.items():
                if len(subs) > 1:
                    subs.sort(key=lambda sub: sub.id)
                    for sub in subs[1:]:
                        sub.delete()
                    changed = True
                by_class[cl] = subs[0]
            my_subscriptions = by_class.values()
            missing = set(needed_classes) - my_subscriptions_classes
            if not missing:
                return my_subscriptions, changed
            changed = True
            try:
                with transaction.manager:
                    defaults = [
                        cls(
                            discussion_id=discussion_id,
                            user_id=my_id,
                            creation_origin=NotificationCreationOrigin.DISCUSSION_DEFAULT,
                            status=(NotificationSubscriptionStatus.ACTIVE
                                    if subscribed[cls.__mapper__.polymorphic_identity.name]
                                    else NotificationSubscriptionStatus.INACTIVE_DFT))
                        for cls in missing
                    ]
                    for d in defaults:
                        self.db.add(d)
            except ObjectNotUniqueError as e:
                transaction.abort()
                # Sleep some time to avoid race condition
                from time import sleep
                from random import random
                sleep(random()/10.0)


Index("user_template", "discussion_id", "role_id")


class PartnerOrganization(DiscussionBoundBase):
    """A corporate entity"""
    __tablename__ = "partner_organization"
    id = Column(Integer, primary_key=True,
        info={'rdf': QuadMapPatternS(None, ASSEMBL.db_id)})

    discussion_id = Column(Integer, ForeignKey(
        "discussion.id", ondelete='CASCADE'), nullable=False, index=True,
        info={'rdf': QuadMapPatternS(None, DCTERMS.contributor)})
    discussion = relationship(
        'Discussion', backref=backref(
            'partner_organizations', cascade="all, delete-orphan"),
        info={'rdf': QuadMapPatternS(None, ASSEMBL.in_conversation)})

    name = Column(CoerceUnicode(256),
        info={'rdf': QuadMapPatternS(None, FOAF.name)})

    description = Column(UnicodeText,
        info={'rdf': QuadMapPatternS(None, DCTERMS.description)})

    logo = Column(URLString(),
        info={'rdf': QuadMapPatternS(None, FOAF.logo)})

    homepage = Column(URLString(),
        info={'rdf': QuadMapPatternS(None, FOAF.homepage)})

    is_initiator = Column(Boolean)

    def unique_query(self):
        query, _ = super(PartnerOrganization, self).unique_query()
        return query.filter_by(name=self.name), True

    def get_discussion_id(self):
        return self.discussion_id or self.discussion.id

    @classmethod
    def get_discussion_conditions(cls, discussion_id, alias_maker=None):
        return (cls.discussion_id == discussion_id,)

    crud_permissions = CrudPermissions(P_ADMIN_DISC)


class LanguagePreferenceOrder(IntEnum):
    Explicit = 0
    Cookie = 1
    Parameter = 2
    DeducedFromTranslation = 3
    OS_Default = 4
    Discussion = 5

LanguagePreferenceOrder.unique_prefs = (
    LanguagePreferenceOrder.Cookie,
    LanguagePreferenceOrder.Parameter,
    LanguagePreferenceOrder.OS_Default)


class LanguagePreferenceCollection(object):
    @abstractmethod
    def find_locale(self, locale):
        pass

    @classmethod
    def getCurrent(cls, req=None):
        from pyramid.threadlocal import get_current_request
        from pyramid.security import Everyone
        # Very very hackish, but this call is costly and frequent.
        # Let's cache it in the request. Useful for view_def use.
        if req is None:
            req = get_current_request()
        assert req
        if getattr(req, "lang_prefs", 0) is 0:
            user_id = req.authenticated_userid
            if user_id and user_id != Everyone:
                try:
                    req.lang_prefs = UserLanguagePreferenceCollection(user_id)
                    return req.lang_prefs
                except Exception:
                    capture_exception()
            locale = locale_negotiator(req)
            req.lang_prefs = LanguagePreferenceCollectionWithDefault(locale)
        return req.lang_prefs

    @abstractmethod
    def default_locale_code(self):
        pass


class LanguagePreferenceCollectionWithDefault(LanguagePreferenceCollection):
    def __init__(self, locale_code):
        self.default_locale = Locale.get_or_create(locale_code)

    def default_locale_code(self):
        return self.default_locale

    def find_locale(self, locale):
        if Locale.compatible(locale, self.default_locale.code):
            return UserLanguagePreference(
                locale=self.default_locale, locale_id=self.default_locale.id,
                source_of_evidence=LanguagePreferenceOrder.Cookie.value)
        else:
            locale = Locale.get_or_create(locale)
            return UserLanguagePreference(
                locale=locale, locale_id=locale.id,
                translate_to_locale=self.default_locale,
                translate_to=self.default_locale.id,
                source_of_evidence=LanguagePreferenceOrder.Cookie.value)


class UserLanguagePreferenceCollection(LanguagePreferenceCollection):
    def __init__(self, user_id):
        user = User.get(user_id)
        user_prefs = user.language_preference
        assert user_prefs
        user_prefs.sort(reverse=True)
        prefs_by_locale = {
            user_pref.locale_code: user_pref
            for user_pref in user_prefs
        }
        user_prefs.reverse()
        prefs_with_trans = [up for up in user_prefs if up.translate_to]
        prefs_without_trans = [
            up for up in user_prefs if not up.translate_to]
        prefs_without_trans_by_loc = {
            up.locale_code: up for up in prefs_without_trans}
        # First look for translation targets
        for (loc, pref) in prefs_by_locale.items():
            for n, l in enumerate(Locale.decompose_locale(loc)):
                if n == 0:
                    continue
                if l in prefs_by_locale:
                    break
                prefs_by_locale[l] = pref
        for pref in prefs_with_trans:
            for l in Locale.decompose_locale(pref.translate_to_code):
                if l in prefs_without_trans_by_loc:
                    break
                locale = Locale.get_or_create(l)
                new_pref = UserLanguagePreference(
                    locale=locale, locale_id=locale.id,
                    source_of_evidence=
                        LanguagePreferenceOrder.DeducedFromTranslation.value,
                    preferred_order=pref.preferred_order)
                prefs_without_trans.append(new_pref)
                prefs_without_trans_by_loc[l] = new_pref
                if l not in prefs_by_locale:
                    prefs_by_locale[l] = new_pref
        default_pref = None
        if prefs_with_trans:
            prefs_with_trans.sort()
            target_lang_code = prefs_with_trans[0].translate_to_code
            default_pref = prefs_without_trans_by_loc.get(
                target_lang_code, None)
        if not default_pref:
            # using the untranslated locales, if any.
            prefs_without_trans.sort()
            # TODO: Or use discussion locales otherwise?
            # As it stands, the cookie is the fallback.
            default_pref = (
                prefs_without_trans[0] if prefs_without_trans else None)
        self.user_prefs = prefs_by_locale
        self.default_pref = default_pref

    def default_locale_code(self):
        return self.default_pref.locale.code

    def find_locale(self, locale):
        # This code needs to mirror
        # LanguagePreferenceCollection.getPreferenceForLocale
        for locale in Locale.decompose_locale(locale):
            if locale in self.user_prefs:
                return self.user_prefs[locale]
        if self.default_pref is None:
            # this should never actually happen
            return None
        locale = Locale.get_or_create(locale)
        return UserLanguagePreference(
            locale=locale, locale_id=locale.id,
            translate_to_locale=self.default_pref.locale,
            translate_to=self.default_pref.locale.id,
            source_of_evidence=self.default_pref.source_of_evidence,
            user=None)  # Do not give the user or this gets added to session


class UserLanguagePreference(Base):
    __tablename__ = 'user_language_preference'
    __table_args__ = (UniqueConstraint(
        'user_id', 'locale_id', 'source_of_evidence'), )

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer, ForeignKey(
            User.id, ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)

    locale_id = Column(Integer, ForeignKey('locale.id',
                       ondelete='CASCADE', onupdate='CASCADE'),
                       nullable=False, index=False)

    locale = relationship(Locale, foreign_keys=[locale_id])

    translate_to = Column(
        Integer, ForeignKey(
            'locale.id', onupdate='CASCADE', ondelete='CASCADE'))

    translate_to_locale = relationship(Locale, foreign_keys=[translate_to])

    # Sort the preferences within a source_of_evidence
    # Descending order preference, 0 - is the highest
    preferred_order = Column(Integer, nullable=False, default=0)

    # This is the actual evidence source, whose contract is defined in
    # LanguagePreferenceOrder. They have priority over preferred_order
    source_of_evidence = Column(Integer, nullable=False)

    user = relationship('User', backref=backref(
                        'language_preference',
                        cascade='all, delete-orphan',
                        order_by=source_of_evidence))

    crud_permissions = CrudPermissions(
            P_READ, P_SYSADMIN, P_SYSADMIN, P_SYSADMIN,
            P_READ, P_READ, P_READ)

    def is_owner(self, user_id):
        return user_id == self.user_id

    def __cmp__(self, other):
        if not isinstance(other, self.__class__):
            return -1
        s = self.source_of_evidence - other.source_of_evidence
        if s:
            return s
        s = (self.preferred_order or 0) - (other.preferred_order or 0)
        if s:
            return s
        s = (self.id or 0) - (other.id or 0)
        if s:
            return s
        return id(self) - id(other)

    # def set_priority_order(self, code):
    #     # code can be ignored. This value should be updated for each user
    #     # as each preferred language is committed
    #     current_languages = self.db.query(UserLanguagePreference).\
    #                         filter_by(user=self.user).\
    #                         order_by(self.preferred_order).all()

    #     if self.source_of_evidence == 0:
    #         pass

    @property
    def locale_code(self):
        if self.locale_id:
            return Locale.code_for_id(self.locale_id)
        return self.locale.code

    @locale_code.setter
    def locale_code(self, code):
        assert(code)
        posix = to_posix_string(code)
        locale = Locale.get_or_create(posix, self.db)
        self.locale = locale
        self.locale_id = locale.id

    @property
    def translate_to_code(self):
        if self.translate_to:
            return Locale.code_for_id(self.translate_to)
        elif self.translate_to_locale:
            return self.translate_to_locale.code

    @translate_to_code.setter
    def translate_to_code(self, code):
        if code:
            posix = to_posix_string(code)
            assert posix
            locale = Locale.get_or_create(posix, self.db)
            self.translate_to_locale = locale
            self.translate_to = locale.id
        else:
            self.translate_to_locale = None
            self.translate_to = None

    def unique_query(self):
        query, _ = super(UserLanguagePreference, self).unique_query()
        query = query.filter_by(
            user_id=self.user_id or self.user.id,
            locale_id=self.locale_id or self.locale.id,
            source_of_evidence=self.source_of_evidence)
        return query, True

    def __repr__(self):
        return \
            "{user_id: %d, locale_id: %s, translated_to: %s "\
            "source_of_evidence: %s, preferred_order: %d}" % (
                self.user_id or -1,
                self.locale_code, self.translate_to_code,
                LanguagePreferenceOrder(self.source_of_evidence).name,
                self.preferred_order or 0
            )
