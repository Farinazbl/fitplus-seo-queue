from . import base
from .bale import BaleConnector
from .wordpress import WordPressConnector

base.register("bale", BaleConnector())
base.register("wordpress", WordPressConnector())
