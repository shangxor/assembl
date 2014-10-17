from celery import Celery

from . import init_task_config, config_celery_app

# broker specified
imap_celery_app = Celery('celery_tasks.imap')

@imap_celery_app.task(ignore_result=True)
def import_mails(mbox_id, only_new=True):
    init_task_config()
    from ..models import IMAPMailbox
    # in case of previous issues
    IMAPMailbox.db.rollback()
    mailbox = IMAPMailbox.get(mbox_id)
    assert mailbox != None
    IMAPMailbox.do_import_content(mailbox, only_new)

def includeme(config):
    config_celery_app(imap_celery_app, config.registry.settings)
