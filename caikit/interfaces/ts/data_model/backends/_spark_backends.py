# Copyright The Caikit Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Core data model backends backed by pyspark.sql.DataFrame.

This module is not intended for direct importing. It's used
by the caikit ts datamodel. Directly importing this module
will force a hard spark dependency which we do not want
to do.
"""

# Standard
from typing import Any, Iterable, Optional, Tuple, Type, Union

# Third Party
import pandas as pd

# this import is ok because this module is NOT proactively imported
import pyspark

# First Party
import alog

# Local
from .....core.data_model import ProducerId
from .....core.exceptions import error_handler
from .._single_timeseries import SingleTimeSeries
from .base import MultiTimeSeriesBackendBase, TimeSeriesBackendBase
from .dfcache import EnsureCached
from .pandas_backends import PandasMultiTimeSeriesBackend, PandasTimeSeriesBackend
from .util import mock_pd_groupby

log = alog.use_channel("SPBCK")
error = error_handler.get(log)


class SparkMultiTimeSeriesBackend(MultiTimeSeriesBackendBase):
    def __init__(
        self,
        data_frame: pyspark.sql.DataFrame,
        key_column: Union[Iterable[str], str],
        timestamp_column: str = None,
        value_columns: Optional[Iterable[str]] = None,
        ids: Optional[Union[Iterable[int], Iterable[str]]] = None,
        producer_id: Optional[Union[Tuple[str, str], ProducerId]] = None,
    ):
        error.type_check("<COR77829913F>", pyspark.sql.DataFrame, data_frame=data_frame)

        # for param validation
        _ = PandasMultiTimeSeriesBackend(
            data_frame=pd.DataFrame(columns=data_frame.columns),
            key_column=key_column,
            timestamp_column=timestamp_column,
            value_columns=value_columns,
            ids=ids,
            producer_id=producer_id,
        )

        self._pyspark_df: pyspark.sql.DataFrame = data_frame
        # for tapping into pandas api call when needed
        self._pyspark_pandas_df = self._pyspark_df.pandas_api()
        self._key_column = key_column
        self._timestamp_column = timestamp_column
        # pylint: disable=duplicate-code
        self._value_columns = self._value_columns = value_columns or [
            col
            for col in data_frame.columns
            if col != timestamp_column and col not in key_column
        ]
        self._ids = [] if ids is None else ids
        self._producer_id = (
            producer_id
            if isinstance(producer_id, ProducerId)
            else (ProducerId(*producer_id) if producer_id is not None else None)
        )

    def get_attribute(
        self, data_model_class: Type["MultiTimeSeries"], name: str
    ) -> Any:
        # pylint: disable=duplicate-code
        if isinstance(self._key_column, str):
            key_columns = [self._key_column]
        else:
            key_columns = self._key_column

        if name == "timeseries":
            result = []

            if len(key_columns) == 0:
                with EnsureCached(self._pyspark_df) as _:
                    backend = SparkTimeSeriesBackend(
                        data_frame=self._pyspark_df,
                        timestamp_column=self._timestamp_column,
                        value_columns=self._value_columns,
                    )
                    result.append(SingleTimeSeries(_backend=backend))
            else:
                with EnsureCached(self._pyspark_df) as _:
                    for ids, spark_df in mock_pd_groupby(
                        self._pyspark_df, by=key_columns
                    ):
                        k = ids
                        if isinstance(k, (str, int)):
                            k = [k]
                        backend = SparkTimeSeriesBackend(
                            data_frame=spark_df,
                            timestamp_column=self._timestamp_column,
                            value_columns=self._value_columns,
                            ids=k,
                        )
                        result.append(SingleTimeSeries(_backend=backend))
            return result

        if name == "id_labels":
            return key_columns

        # If requesting producer_id or ids, just return the stored value
        if name == "producer_id":
            return self._producer_id

        raise ValueError(f"Provided an attribute name that does not exist - {name}")

    def as_pandas(self) -> Tuple[pd.DataFrame, Iterable[str], str, Iterable[str]]:
        return (
            self._pyspark_df.toPandas(),
            self._key_column,
            self._timestamp_column,
            self._value_columns,
        )


class SparkTimeSeriesBackend(TimeSeriesBackendBase):
    """The SparkTimeSeries is responsible for managing the standard
    in-memory representation of a TimeSeries using a spark backend compute engine.
    """

    def __init__(
        self,
        data_frame: pyspark.sql.DataFrame,
        timestamp_column: str = None,
        value_columns: Optional[Iterable[str]] = None,
        ids: Optional[Iterable[int]] = None,
    ):
        """At init time, hold onto the data frame as well as the arguments that
        tell where the time and values live

        Args:
            data_frame:  pyspark.sql.DataFrame
                The raw data frame
            timestamp_column:  Optional[str]
                The name of the column holding the timestamps. If set to None, timestamps will be
                assigned based on the rows index (default is None)
            value_columns:  Optional[Iterable[str]]
                A sequence of names of columns to hold as values
            ids:  Optional[iterable[int]]
                A sequence of numeric IDs associated with this TimeSeries
        """

        # Validators special to this class
        error.type_check("<COR11947329E>", pyspark.sql.DataFrame, data_frame=data_frame)

        self._pyspark_df: pyspark.sql.DataFrame = data_frame

        # for tapping into pandas api call when needed
        self._pyspark_pandas_df = self._pyspark_df.pandas_api()

        # this will give us basic parameter validation
        self._pdbackend_helper = PandasTimeSeriesBackend(
            data_frame=pd.DataFrame(columns=data_frame.columns),
            value_columns=value_columns,
            timestamp_column=str(timestamp_column)
            if timestamp_column is not None
            else timestamp_column,
            ids=ids,
        )

    def get_attribute(
        self, data_model_class: Type["SingleTimeSeries"], name: str
    ) -> Any:
        """When fetching a data attribute from the timeseries, this aliases to
        the appropriate set of backend wrappers for the various fields.
        """

        with EnsureCached(self._pyspark_df) as _:
            return self._pdbackend_helper.get_attribute(
                data_model_class=data_model_class,
                name=name,
                external_df=self._pyspark_pandas_df,
            )

    def as_pandas(self) -> Tuple[pd.DataFrame, str, Iterable[str]]:
        return (
            self._pyspark_df.toPandas(),
            self._pdbackend_helper._timestamp_column,
            self._pdbackend_helper._value_columns,
        )
