"""Utilities related to manageing spark DataFrame caching"""

# Third Party
from pyspark.sql import DataFrame


class EnsureCached:
    """Will ensure that a given dataframe is cached.
    If dataframe is already cached it does nothing. If it's not
    cached, it will cache it and then uncache the object when
    the EnsureCached object container goes out of scope. Users
    must utilize the with pattern of access.

    Example:
    ```python
        with EnsureCached(df) as _:
            # do dataframey sorts of things on df
            # it's guarenteed to be cached
            # inside this block
        # that's it, you're done.
        # df remains cached if it already was
        # or it's no longer cached if it wasn't
        # before entering the with block above.
    ```
    """

    def __init__(self, dataframe: DataFrame):
        self._did_cache = False
        self._df = dataframe
        if hasattr(dataframe, "cache"):
            if not self._df.is_cached:
                self._df.cache()
                self._did_cache = True

    def __enter__(self):
        return self._df

    def __exit__(self, exc_type, exc_value, traceback):
        if self._did_cache:
            self._df.unpersist()