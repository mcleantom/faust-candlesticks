import random

import faust
from datetime import timedelta, datetime
from time import time


class StockTransaction(faust.Record):
    date: datetime
    price: float
    stock: str


class Candlestick(faust.Record):
    start_aggregation_period_timestamp: datetime
    end_aggregation_period_timestamp: datetime
    start_price: float
    high_price: float
    low_price: float
    end_price: float
    aggregation_count: int

    def aggregate_transaction(self, stock_transaction: StockTransaction):
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


TOPIC = 'raw-event'
SINK = 'agg-event'
TABLE = 'tumbling_table'
KAFKA = 'kafka://localhost:9092'
CLEANUP_INTERVAL = 1.0
WINDOW = 10
WINDOW_EXPIRES = 20
PARTITIONS = 1


app = faust.App(TABLE, broker=KAFKA, topic_partitions=1, version=1)
app.conf.table_cleanup_interval = CLEANUP_INTERVAL


source = app.topic(TOPIC, value_type=StockTransaction, key_type=str)
sink = app.topic(SINK, value_type=Candlestick)


def window_processor(stock, candlestick):
    sink.send_soon(value=candlestick)


candlesticks = app.Table(
    TABLE,
    default=lambda: Candlestick(
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
    price = random.uniform(100, 200)
    await source.send(
        key="AAPL",
        value=StockTransaction(stock="AAPL", price=price, date=int(time()))
    )


@app.agent(source)
async def consume(transactions):
    transaction: StockTransaction
    async for transaction in transactions:
        candlestick_window = candlesticks[transaction.stock]
        current_window = candlestick_window.current()
        current_window.aggregate_transaction(transaction)
        candlesticks[transaction.stock] = current_window


if __name__ == '__main__':
    app.main()
