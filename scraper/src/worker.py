""" Defines a worker that subscribes to instrument IDs sent over RabbitMQ and either fetches
quotes, popularity, or stores the ID in a database. """

import datetime
from functools import reduce
from json.decoder import JSONDecodeError
from pprint import pprint

import click
import pika
import pymongo
from pymongo.errors import BulkWriteError
import requests

from Robinhood import Robinhood
from Robinhood.exceptions import InvalidTickerSymbol

from common import parse_throttle_res
from db import get_db, set_popularities_finished, set_quotes_finished, unlock_cache
from utils import parse_instrument_url, parse_updated_at, pluck, DESIRED_QUOTE_KEYS

INDEX_COL = get_db()["index"]

TRADER = Robinhood()


def store_popularities(popularity_map: dict, collection: pymongo.collection.Collection):
    """ Creates an entry in the database for the popularity. """

    timestamp = datetime.datetime.utcnow()
    pprint(popularity_map)
    mapped_documents = map(
        lambda key: {
            "timestamp": timestamp,
            "instrument_id": key,
            "popularity": popularity_map[key],
        },
        popularity_map.keys(),
    )

    collection.insert_many(mapped_documents)


def store_quotes(quotes: list, collection: pymongo.collection.Collection):
    """ Creates entries in the database for the provided quotes. """

    def map_quote(quote: dict) -> dict:
        instrument_id = parse_instrument_url(quote["instrument"])

        plucked = {"instrument_id": instrument_id, **pluck(DESIRED_QUOTE_KEYS, quote)}
        plucked["updated_at"] = parse_updated_at(plucked["updated_at"])
        return plucked

    quotes = list(filter(lambda quote: quote != None, quotes))

    def format_quote(quote: dict) -> dict:
        return {"symbol": quote["symbol"], "bid": quote["bid_price"], "ask": quote["ask_price"]}

    pprint(list(map(format_quote, quotes)))

    # Update the index collection with up-to-date tradability info
    timestamp = datetime.datetime.utcnow()

    def update_index_symbol(datum: dict) -> pymongo.operations.UpdateOne:
        data = {
            "timestamp": timestamp,
            "has_traded": datum.get("has_traded"),
            "updated_at": parse_updated_at(datum.get("updated_at")),
            "trading_halted": datum.get("trading_halted"),
        }
        instrument_id = parse_instrument_url(datum["instrument"])

        return pymongo.operations.UpdateOne({"instrument_id": instrument_id}, {"$set": data})

    ops = list(map(update_index_symbol, quotes))
    INDEX_COL.bulk_write(ops, ordered=False)

    quotes = list(map(map_quote, quotes))
    try:
        collection.insert_many(quotes, ordered=False)
    except BulkWriteError as bwe:
        for err in bwe.details["writeErrors"]:
            if "duplicate key" not in err["errmsg"]:
                print("ERROR: Unhandled exception occured during batch write:")
                pprint(err)


def fetch_popularity(
    instrument_ids: str,
    collection: pymongo.collection.Collection,
    sleep,
    worker_request_cooldown_seconds=1.0,
):
    if instrument_ids == "__DONE":
        print('Received DONE message for popularity fetching; marking as complete in Redis...')
        set_popularities_finished()
        return

    url = "https://api.robinhood.com/instruments/popularity/?ids={}".format(instrument_ids)

    def reduce_popularity(acc: dict, datum: dict) -> dict:
        instrument_id = parse_instrument_url(datum["instrument"])

        return {**acc, instrument_id: datum["num_open_positions"]}

    def call_self():
        """ In the case of some kind of error, wait 30 seconds and then re-call ourself to try
        again. """

        sleep(30)
        fetch_popularity(
            instrument_ids,
            collection,
            sleep,
            worker_request_cooldown_seconds=worker_request_cooldown_seconds,
        )

    try:
        res = TRADER.get_url(url)
        popularities = reduce(reduce_popularity, res["results"], {})
        store_popularities(popularities, collection)
        sleep(worker_request_cooldown_seconds)
    except KeyError:  # Likely a ratelimit issue; cooldown.
        if not res.get("results"):
            print("ERROR: Unexpected response received from popularity request: {}".format(res))
            sleep(120)
            return

        print(res)
        cooldown_seconds = parse_throttle_res(res["detail"])
        print(
            "Popularity fetch request failed; waiting for {} second cooldown...".format(
                cooldown_seconds
            )
        )
        sleep(cooldown_seconds)

        fetch_popularity(
            instrument_ids,
            collection,
            sleep,
            worker_request_cooldown_seconds=worker_request_cooldown_seconds,
        )
    except requests.exceptions.ReadTimeout:
        print("Read timeout while fetching popularity... Sleeping 30 seconds and re-trying.")
        call_self()
    except TypeError:  # They sent back some broken data; just ignore it.
        print("Robinhood sent back garbage; ignoring.")
        call_self()
    except JSONDecodeError:
        print("Robinhood API sending back HTML; backing off.")
        call_self()


def fetch_quote(
    symbols: str,
    collection: pymongo.collection.Collection,
    sleep,
    worker_request_cooldown_seconds=1.0,
):
    if symbols == "__DONE":
        print('Received DONE message for quote fetching; marking as complete in Redis...')
        set_quotes_finished()
        return

    try:
        res = TRADER.quote_data(symbols)
        quotes = res["results"]
        store_quotes(quotes, collection)

        sleep(worker_request_cooldown_seconds)
    except KeyError:  # Likely a ratelimit issue; cooldown.
        if not res.get("detail"):
            print("ERROR: Unexpected response received from quote request: {}".format(res))
            sleep(120)
            return

        cooldown_seconds = parse_throttle_res(res["detail"])
        print(
            "Quote fetch request failed; waiting for {} second cooldown...".format(cooldown_seconds)
        )
        sleep(cooldown_seconds)

        fetch_quote(
            symbols,
            collection,
            sleep,
            worker_request_cooldown_seconds=worker_request_cooldown_seconds,
        )
    except InvalidTickerSymbol:
        print("Error while fetching symbols: {}".format(symbols))
    except requests.exceptions.ReadTimeout:
        print("Read timeout while fetching quotes... Sleeping 30 seconds and re-trying.")
        sleep(30)
        fetch_quote(
            symbols,
            collection,
            sleep,
            worker_request_cooldown_seconds=worker_request_cooldown_seconds,
        )


WORK_CBS = {
    "popularity": (fetch_popularity, "popularity", "instrument_ids"),
    "quote": (fetch_quote, "quotes", "symbols"),
}


@click.command()
@click.option("--mode", type=click.Choice(["quote", "popularity"]), default="popularity")
@click.option("--rabbitmq_host", default="localhost")
@click.option("--rabbitmq_port", type=click.INT, default=5672)
@click.option("--worker_request_cooldown_seconds", type=click.FLOAT, default=1.0)
def cli(mode: str, rabbitmq_host: str, rabbitmq_port: str, worker_request_cooldown_seconds: float):
    print('Unlocking cache...')
    unlock_cache()

    rabbitmq_connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=rabbitmq_host, port=rabbitmq_port)
    )
    rabbitmq_channel = rabbitmq_connection.channel()

    (work_cb, collection_name, channel_name) = WORK_CBS[mode]
    db = get_db()
    collection = db[collection_name]
    rabbitmq_channel.queue_declare(queue=channel_name)

    def handle_work(_channel, _method, _properties, body):
        work_cb(
            body.decode("utf-8"),
            collection,
            rabbitmq_connection.sleep,
            worker_request_cooldown_seconds=worker_request_cooldown_seconds,
        )

    rabbitmq_channel.basic_consume(handle_work, queue=channel_name, no_ack=True)
    rabbitmq_channel.start_consuming()


if __name__ == "__main__":
    cli()  # pylint: disable=E1120
