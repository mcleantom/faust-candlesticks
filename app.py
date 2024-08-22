import faust
from datetime import timedelta, datetime
from time import time


class RawModel(faust.Record):
    date: datetime
    value: str


class AggModel(faust.Record):
    date: datetime
    page: str
    count: int


TOPIC = 'raw-event'
SINK = 'agg-event'
TABLE = 'tumbling_table'
KAFKA = 'kafka://localhost:9092'
CLEANUP_INTERVAL = 1.0
WINDOW = 10
WINDOW_EXPIRES = 1
PARTITIONS = 1


app = faust.App(TABLE, broker=KAFKA, topic_partitions=1, version=1)
app.conf.table_cleanup_interval = CLEANUP_INTERVAL


source = app.topic(TOPIC, value_type=RawModel)
sink = app.topic(SINK, value_type=AggModel)


def window_processor(key, count):
    sink.send_soon(value=AggModel(page=key, count=count, date=int(time())))


views = app.Table(
    'views',
    default=int,
    partitions=1,
    on_window_close=window_processor
).tumbling(
    10,
    expires=timedelta(seconds=1)
).relative_to_field(RawModel.date)


@app.timer(0.1)
async def produce():
    await source.send(value=RawModel(value="https://www.google.com", date=int(time())))


@app.agent(source)
async def consume(pages):
    async for page_url in pages:
        views[page_url] = views[page_url].value() + 1


if __name__ == '__main__':
    app.main()
