import logging
import time
import threading
import traceback
import base64
import configparser
import cbint.globals

from cbint.integration import Integration
from cbint.binary_database import db
from cbint.binary_database import BinaryDetonationResult
from cbint.binary_collector import BinaryCollector
from cbint.rpc_server import RpcServer
from cbint.flask_feed import create_flask_app
from cbapi.response.rest_api import CbResponseAPI
from cbapi.response.models import Binary
from cbapi.errors import *

from cbint.cbfeeds import CbReport, CbFeed

logger = logging.getLogger(__name__)


class BinaryDetonation(Integration):
    def __init__(self):
        super().__init__()
        #
        # Connect to the sqlite db and make sure the tables are created
        #

        logger.debug("Attempting to connect to sqlite database...")
        try:
            db.connect()
            db.create_tables([BinaryDetonationResult])
            self.db_object = db
        except:
            logger.error(traceback.format_exc())
        logger.debug("Connected to sqlite database")

        #
        # Create a CbApi instance for Cb Response
        #
        logger.debug("Attempting to connect to Cb Response...")
        cb = CbResponseAPI(profile='eap')
        self.cb_response_api = cb
        logger.debug("Connected to Cb Response")

        #
        # Create a Binary Collector and start it
        #
        logger.debug("Starting binary collector...")
        bc = BinaryCollector(cb=cb, query='')
        bc.start()
        self.binary_collector = bc
        logger.debug("Binary Collector has started")

        logger.debug("Starting RPC server...")
        rpc_server = RpcServer()
        rpc_server.start(50051)
        logger.debug("RPC Server has started")

        self.flask_feed = create_flask_app()
        logger.debug(self.flask_feed)
        flask_thread = threading.Thread(target=self.flask_feed.run,
                                        kwargs={"port": 5000, "debug": False, "use_reloader": False})
        flask_thread.daemon = True
        flask_thread.start()
        logger.debug("init complete")

    def set_feed_info(self, name, summary='', tech_data='', provider_url='', icon_path="", display_name=''):
        """
        :param name:
        :param summary:
        :param tech_data:
        :param provider_url:
        :param icon_path:
        :param display_name:
        :return:
        """
        icon = base64.b64encode(open(icon_path, 'rb').read()).decode('utf-8')

        self.feedinfo = {"name": name,
                         "summary": summary,
                         "tech_data": tech_data,
                         "provider_url": provider_url,
                         "icon": icon,
                         "display_name": display_name}

    def binaries_to_scan(self):
        """
        :return:
        """
        cb = CbResponseAPI(profile='eap')

        while (True):
            for detonation in BinaryDetonationResult.select():
                md5 = detonation.md5
                binary_query = cb.select(Binary).where(f"md5:{md5}")
                if binary_query:
                    try:
                        binary_query[0].file
                    except ObjectNotFoundError:
                        continue

                    yield (binary_query[0])
            time.sleep(1)

    def generate_feed_from_db(self):
        """
        :return:
        """
        self.reports = []
        feed_results = BinaryDetonationResult.select().where(BinaryDetonationResult.score > 0)

        for result in feed_results:
            fields = {'iocs': {'md5': [result.md5]},
                      'score': result.score,
                      'timestamp': int(time.mktime(time.gmtime())),
                      'link': '',
                      'id': f'binary_{result.md5}',
                      'title': '',
                      'description': result.success_msg
                      }

            self.reports.append(CbReport(**fields))
            self.feed = CbFeed(self.feedinfo, self.reports)

            with open("feed/feed.json", 'w') as fp:
                fp.write(self.feed.dump())

    def report_successful_detonation(self, md5: str, score: int = 0, success_msg: str = ''):
        """
        :param md5:
        :param score:
        :param success_msg:
        :return:
        """
        bdr = BinaryDetonationResult.get(BinaryDetonationResult.md5 == md5)
        bdr.score = score
        bdr.success_msg = success_msg
        bdr.save()

        #
        # We want to update the feed if a new reports comes in with score > 0
        #
        if score > 0:
            self.generate_feed_from_db()

    def report_failure_detonation(self, md5: str, score: int = 0, error_msg: str = ''):
        """
        :param md5:
        :param score:
        :param error_msg:
        :return:
        """
        bdr = BinaryDetonationResult.get(BinaryDetonationResult.md5 == md5)
        bdr.score = score
        bdr.last_error_msg = error_msg
        bdr.save()