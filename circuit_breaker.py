# Паттерн 'Circuit Breaker'
# Если обратиться к url через метод CircuitBreaker.external_request,
# то при NUMBER_OF_ATTEMPTS неудачных попытках доступа, дальнейшие будут заблокированы
# на BREAK_TIME секунд.
#
# При этом выбрасывается исключение CircuitBreakerException, и выполнение метода апи,
# который вызвал CircuitBreaker.external_request немедленно прекращается

from urllib.parse import urlparse
from time import sleep, time
from functools import wraps

import requests
from requests.exceptions import RequestException

NUMBER_OF_ATTEMPTS = 2
TIME_BETWEEN_ATTEMPTS = 1
BREAK_TIME = 30

CIRCUIT_BREAK_STATUS_CODE = 555

class CircuitBreakerException(Exception):
    pass


def handles_circuit_break(func):
    @wraps(func)
    def wrap(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except CircuitBreakerException as e:
            return {"message": str(e)}, CIRCUIT_BREAK_STATUS_CODE
    return wrap


class CircuitBreaker:
    def __init__(self):
        self.circuit_breaker_cache = {}
        self.circuit_breaker_timeout = BREAK_TIME

    def external_request(self, method, url, **kwargs):
        service = urlparse(url).netloc

        if service in self.circuit_breaker_cache:
            time_passed = time() - self.circuit_breaker_cache[service]
            if time_passed < self.circuit_breaker_timeout:
                raise CircuitBreakerException(
                    f"Curcuit breaker to '{service}' still active, "
                    f"please wait {int(self.circuit_breaker_timeout - time_passed)} seconds"
                )
            del self.circuit_breaker_cache[service]

        exc = None
        for _ in range(NUMBER_OF_ATTEMPTS):
            try:
                resp = requests.request(method, url, **kwargs)
                if resp.status_code == CIRCUIT_BREAK_STATUS_CODE:
                    raise CircuitBreakerException(str(resp.text))
                return resp
            except RequestException as e:
                exc = e
                sleep(TIME_BETWEEN_ATTEMPTS)

        self.circuit_breaker_cache[service] = time()
        raise CircuitBreakerException(
            f"Problem with '{service}'. Circuit breaker activated. "
            f"Exception: {repr(exc)}"
        )

