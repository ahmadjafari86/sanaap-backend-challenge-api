from .recover_outbox_uploads import Command as RecoveryCommand


class Command(RecoveryCommand):
    help = "Compatibility alias for durable upload recovery; never publishes directly."
