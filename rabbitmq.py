# тут подключение к rabbitmq и методы для работы с ним

import os
import json

import pika

QUEUE_URL = os.environ.get("QUEUE_URL", "amqp://localhost")
QUEUE_NAME = "warranty"
print(f"RabbitMQ url: {QUEUE_URL} ($QUEUE_URL). Queue name: '{QUEUE_NAME}'")


class Queue:
    def __enter__(self):
        self.connection = pika.BlockingConnection(pika.URLParameters(QUEUE_URL))
        self.channel = self.connection.channel()
        self.channel.queue_declare(queue=QUEUE_NAME)
        return self

    def __exit__(self, *args):
        self.connection.close()

    def publish(self, data: "json dict"):
        self.channel.basic_publish(exchange='', routing_key=QUEUE_NAME, body=json.dumps(data))

    def consume(self) -> "generator (json)":
        for method_frame, properties, body in self.channel.consume(QUEUE_NAME, inactivity_timeout=0):
            if not method_frame:
                break
            yield json.loads(body)
            self.channel.basic_ack(method_frame.delivery_tag)


class TestQueue:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def publish(self, data):
        pass

    def consume(self):
        return iter([])
