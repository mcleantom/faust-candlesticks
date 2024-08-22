import random

import faust
import aiohttp
from datetime import timedelta, datetime
from time import time


TOPIC = 'raw-event'
SINK = 'agg-event'
TABLE = 'tumbling_table'
KAFKA = 'kafka://localhost:9092'
CLEANUP_INTERVAL = 1.0
WINDOW = 10
WINDOW_EXPIRES = 20
PARTITIONS = 1
INFLUX_URL = "http://localhost:8086"
INFLUX_TOKEN = "HFvCfIS2vjgxLh871fXQNLiX0XYpjTofZOE759w8XQi6lkw9YrDO9uE99TjRNMzE5JGEFLh6OXaabCitUZ0PDM8BMm0TeyDDAug7GrJQXwMVmmjlAxklAfcbLwab2may"
INFLUX_ORG = "candlesticks"
INFLUX_BUCKET = "candlesticks"


class StockTransaction(faust.Record):
    date: datetime
    price: float
    stock: str


class Candlestick(faust.Record):
    period: timedelta
    stock: str
    start_aggregation_period_timestamp: datetime
    end_aggregation_period_timestamp: datetime
    start_price: float
    high_price: float
    low_price: float
    end_price: float
    aggregation_count: int

    def aggregate_transaction(self, stock_transaction: StockTransaction):
        self.stock = stock_transaction.stock
        unit_price = stock_transaction.price

        if self.aggregation_count == 0:
            self.start_aggregation_period_timestamp = stock_transaction.date
            self.end_aggregation_period_timestamp = stock_transaction.date
            self.start_price = unit_price
            self.low_price = unit_price
            self.end_price = unit_price

        if self.start_aggregation_period_timestamp > stock_transaction.date:
            self.start_aggregation_period_timestamp = stock_transaction.date
            self.start_price = unit_price

        if self.end_aggregation_period_timestamp < stock_transaction.date:
            self.end_aggregation_period_timestamp = stock_transaction.date
            self.end_price = unit_price

        self.high_price = max(self.high_price or unit_price, unit_price)
        self.low_price = min(self.low_price or unit_price, unit_price)
        self.aggregation_count += 1


app = faust.App(TABLE, broker=KAFKA, topic_partitions=1, version=1)
app.conf.table_cleanup_interval = CLEANUP_INTERVAL

source = app.topic(TOPIC, value_type=StockTransaction, key_type=str)
sink = app.topic(SINK, value_type=Candlestick)


async def window_processor(stock, candlestick):
    print(candlestick)

    try:
        async with aiohttp.ClientSession() as session:
            influx_candle = (
                f"{INFLUX_BUCKET},period={candlestick.period},stock={candlestick.stock} "
                f"start_aggregation_period_timestamp={candlestick.start_aggregation_period_timestamp},"
                f"end_aggregation_period_timestamp={candlestick.end_aggregation_period_timestamp},"
                f"start_price={candlestick.start_price},"
                f"high_price={candlestick.high_price},"
                f"low_price={candlestick.low_price},"
                f"end_price={candlestick.end_price},"
                f"aggregation_count={candlestick.aggregation_count} "
                f"{int(time() * 1e9)}"
            )
            headers = {"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"}
            async with session.post(
                f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns",
                headers=headers,
                data=influx_candle
            ) as response:
                if response.status == 204:
                    print(f"Send candlestick to influx")
                else:
                    print(await response.json())
                    response.raise_for_status()
    except Exception as e:
        print(e)
    await sink.send_soon(value=candlestick)


candlesticks = app.Table(
    TABLE,
    default=lambda: Candlestick(
        period=WINDOW,
        stock="",
        start_aggregation_period_timestamp=None,
        end_aggregation_period_timestamp=None,
        start_price=0.0,
        high_price=0.0,
        low_price=0.0,
        end_price=0.0,
        aggregation_count=0
    ),
    partitions=1,
    on_window_close=window_processor
).tumbling(
    timedelta(seconds=WINDOW),
    expires=timedelta(seconds=WINDOW_EXPIRES)
).relative_to_field(StockTransaction.date)


@app.timer(0.1)
async def produce():
    stock = random.choice(["AAPL", "GOOG", "MSFT"])
    price = random.uniform(100, 200)
    await source.send(
        key=stock,
        value=StockTransaction(stock=stock, price=price, date=int(time()))
    )


@app.agent(source)
async def consume(transactions):
    async for transaction in transactions.group_by(StockTransaction.stock):
        candlestick_window = candlesticks[transaction.stock]
        current_window = candlestick_window.current()
        current_window.aggregate_transaction(transaction)
        candlesticks[transaction.stock] = current_window


if __name__ == '__main__':
    app.main()
