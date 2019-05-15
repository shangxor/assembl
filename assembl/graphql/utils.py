from datetime import datetime
import os.path
import pytz

import graphene
from graphene.pyutils.enum import Enum as PyEnum
from graphene.types.scalars import Scalar
from graphene.relay import Node
from graphql.language import ast
from graphql.utils.ast_to_dict import ast_to_dict

from .langstring import langstring_from_input_entries
from assembl import models
from assembl.lib.sentry import capture_exception


publication_states_enum = PyEnum(
    'PublicationStates', [(k, k) for k in models.PublicationStates.values()])
PublicationStates = graphene.Enum.from_enum(publication_states_enum)


class DateTime(Scalar):
    '''DateTime in ISO 8601 format'''

    @staticmethod
    def serialize(dt):
        return dt.replace(tzinfo=pytz.UTC).isoformat()

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return datetime.strptime(
                node.value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC)

    @staticmethod
    def parse_value(value):
        return datetime.strptime(
            value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC)


def abort_transaction_on_exception(fn):
    """Decorator that abort the transaction when an exception is raised in
    a graphql mutation.
    """
    def decorator(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            import transaction
            transaction.abort()
            capture_exception()
            raise

    return decorator


# copied from
# https://github.com/graphql-python/graphene/issues/462#issuecomment-298218524
def collect_fields(node, fragments, variables):
    field = {}
    selection_set = node.get('selection_set') if node else None
    selections = selection_set.get(
        'selections', None) if selection_set else None

    if selections is not None:
        for leaf in selections:
            leaf_kind = leaf.get('kind')
            leaf_name = leaf.get('name', {}).get('value')
            leaf_directives = leaf.get('directives')

            # Check if leaf should be skipped
            # - If name is '__typename'
            # - if @skip directive is used and evaluates to True
            # - if @include directive is used and evaluates to False (not yet implemented!)  # noqa: E501
            should_skip = False
            for directive in leaf_directives:
                if directive.get('name', {}).get('value') == 'skip':
                    for arg in directive.get('arguments', []):
                        arg_value = arg.get('value', {})
                        if arg.get('name', {}).get('value') == 'if':
                            if arg_value.get('kind') == 'Variable':
                                var_name = arg_value.get(
                                    'name', {}).get('value')
                                should_skip = variables.get(
                                    var_name, should_skip)
                            elif arg_value.get('kind') == 'BooleanValue':
                                should_skip = arg_value.get('value')

            if leaf_name != '__typename' and not should_skip:
                if leaf_kind == 'Field':
                    field.update({
                        leaf_name: collect_fields(leaf, fragments, variables)
                    })
                elif leaf_kind == 'FragmentSpread':
                    field.update(collect_fields(
                        fragments[leaf_name], fragments, variables))
                elif leaf_kind == 'InlineFragment':
                    field.update(collect_fields(leaf, fragments, variables))
    return field


def get_fields(info):
    """Return a nested dict of the fields requested by a graphene resolver."""
    fragments = {}
    node = ast_to_dict(info.field_asts[0])

    for name, value in info.fragments.items():
        fragments[name] = ast_to_dict(value)

    fields = collect_fields(node, fragments, info.variable_values)
    return fields


def get_root_thematic_for_phase(phase):
    """Return root thematic for the given phase `phase`.
    """
    if phase.is_thematics_table:
        return phase.root_idea

    return phase.discussion.root_idea


def create_root_thematic(phase):
    """Create the root thematic (hidden) for the given phase `phase`.
    """
    title = u'Phase {}'.format(phase.identifier)
    root_thematic = models.Idea(
        discussion_id=phase.discussion.id,
        title=langstring_from_input_entries(
            [{'locale_code': 'en', 'value': title}]),
        hidden=True)
    phase.discussion.root_idea.children.append(root_thematic)
    phase.is_thematics_table = True
    phase.root_idea = root_thematic
    return root_thematic


def get_attachments_with_purpose(attachments, purpose):
    """Get all attachments with given purpose."""
    return [
        att for att in attachments
        if att.attachmentPurpose == purpose
    ]


def get_attachment_with_purpose(attachments, purpose):
    """Get the first attachment with given purpose."""
    for att in attachments:
        if att.attachmentPurpose == purpose:
            return att

    return None


def create_document(value, context, discussion):
    filename = os.path.basename(context.POST[value].filename)
    mime_type = context.POST[value].type
    document = models.File(
        discussion=discussion,
        mime_type=mime_type,
        title=filename)
    document.add_file_data(context.POST[value].file)
    return document


def create_attachment(discussion, attachment_model, attachment_purpose, context, document_id=None, new_value=None):
    if not document_id and not new_value:
        raise Exception(u"You must provide document_id or new_value")

    if new_value:
        document = create_document(new_value, context, discussion)
        return attachment_model(
            document=document,
            discussion=discussion,
            creator_id=context.authenticated_userid,
            title=document.title,
            attachmentPurpose=attachment_purpose
        )

    if document_id:
        return attachment_model(
            document_id=document_id,
            discussion=discussion,
            creator_id=context.authenticated_userid,
            attachmentPurpose=attachment_purpose
        )


def update_attachment(discussion, attachment_model, new_value, attachments, attachment_purpose, db, context):
    """Update or delete attachment."""
    current_attachment = None
    if attachments:
        purposes_attachments = [
            att for att in attachments if att.attachmentPurpose == attachment_purpose]
        if purposes_attachments:
            current_attachment = purposes_attachments[0]

    if new_value == 'TO_DELETE' and current_attachment:
        # delete the new_value
        current_attachment.document.delete_file()
        db.delete(current_attachment.document)
        db.delete(current_attachment)
        attachments.remove(current_attachment)
    elif new_value == 'TO_DELETE':
        # When the entity is set to be deleted, but does not exist, do nothing (but avoid crashing the transaction)
        return
    else:
        filename = os.path.basename(context.POST[new_value].filename)
        mime_type = context.POST[new_value].type
        document = models.File(
            discussion=discussion,
            mime_type=mime_type,
            title=filename)
        document.add_file_data(context.POST[new_value].file)
        # if there is already an attachment, remove it with the
        # associated document (image)
        if current_attachment:
            current_attachment.document.delete_file()
            db.delete(current_attachment.document)
            attachments.remove(current_attachment)

        attachment = attachment_model(
            document=document,
            discussion=discussion,
            creator_id=context.authenticated_userid,
            title=filename,
            attachmentPurpose=attachment_purpose
        )
        attachments.append(attachment)


def create_idea_announcement(user_id, discussion, idea, title_langstring, description_langstring, quote, summary_langstring):
    """Create an announcement with title and body for an idea.
    """
    idea_announcement = models.IdeaAnnouncement(
        discussion=discussion,
        idea=idea,
        title=title_langstring,
        body=description_langstring,
        creator_id=user_id,
        last_updated_by_id=user_id,
        quote=quote,
        summary=summary_langstring)
    return idea_announcement


def get_primary_id(graphql_id):
    """Get database primary key of an object from its graphql id.
    """
    return int(Node.from_global_id(graphql_id)[1])
