import logging

# Library convention: emit nothing unless the application configures logging
logging.getLogger(__name__).addHandler(logging.NullHandler())
