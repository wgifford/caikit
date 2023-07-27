# Standard
from typing import Iterable, List, Tuple

# Third Party
import numpy as np
import pandas as pd

# First Party
import alog

# Local
from ..data_model._single_timeseries import SingleTimeSeries
from ..data_model.backends.base import MultiTimeSeriesBackendBase
from ..data_model.backends.pandas_backends import PandasMultiTimeSeriesBackend
from ..data_model.backends.util import strip_periodic
from ..data_model.toolkit.optional_dependencies import HAVE_PYSPARK, pyspark
from ..data_model.toolkit.sparkconf import sparkconf_local
from .package import TS_PACKAGE
from caikit.core import DataObjectBase
from caikit.core.data_model import ProducerId, dataobject
from caikit.core.toolkit.errors import error_handler

log = alog.use_channel("TSDM")
error = error_handler.get(log)
S_TO_MS = 1000


@dataobject(package=TS_PACKAGE)
class TimeSeries(DataObjectBase):
    timeseries: List[SingleTimeSeries]
    id_labels: List[str]
    producer_id: ProducerId

    _TEMP_TS_COL = "__ts"

    def __init__(self, *args, **kwargs):
        """Constructing a MultiTimeSeries will currently delegate
        to either a pandas or spark dataframe backend depending
        on whether a native pandas or spark dataframe are passed for
        the first argument respectively.
        """

        if "timeseries" in kwargs:
            self.timeseries = None
            self.id_labels = None
            self.producer_id = None
            is_multi = True
            for k, v in kwargs.items():
                if k == "timeseries" and not isinstance(v, list):
                    is_multi = False
                    setattr(self, k, [v])
                else:
                    setattr(self, k, v)

            # if id_labels was never set, that means we have a single timeseries
            if not is_multi:
                self.id_labels = []
        else:
            error.value_check(
                "<WTS81128386I>",
                len(args) != 0,
                "must have at least the data argument",
                args,
            )
            data_arg = args[0]

            # This will be done if SingleTimeSeries
            if "key_column" not in kwargs or kwargs["key_column"] is None:
                kwargs["key_column"] = []

            if isinstance(data_arg, pd.DataFrame):
                self._backend = PandasMultiTimeSeriesBackend(*args, **kwargs)
            elif HAVE_PYSPARK and isinstance(data_arg, pyspark.sql.DataFrame):
                # Local
                from ..data_model.backends._spark_backends import (
                    SparkMultiTimeSeriesBackend,
                )

                self._backend = SparkMultiTimeSeriesBackend(*args, **kwargs)

    def __len__(self):
        backend = getattr(self, "_backend", None)

        if backend is None:
            return len(self.as_pandas())

        if HAVE_PYSPARK:
            # Local
            from ..data_model.backends._spark_backends import (
                SparkMultiTimeSeriesBackend,
            )

        if isinstance(backend, PandasMultiTimeSeriesBackend):
            return len(backend._df)
        elif HAVE_PYSPARK and isinstance(self._backend, SparkMultiTimeSeriesBackend):
            return backend._pyspark_df.count()
        else:
            error.log_raise(
                "<WTS75394521E>",
                f"Unknown backend {type(backend)}",
            )  # pragma: no cover

    def _get_pd_df(self) -> Tuple[pd.DataFrame, Iterable[str], str, Iterable[str]]:
        """Convert the data to a pandas DataFrame, efficiently if possible"""

        # If there is a backend that knows how to do the conversion, use that
        backend = getattr(self, "_backend", None)
        if backend is not None and isinstance(backend, MultiTimeSeriesBackendBase):
            log.debug("Using backend pandas conversion")
            return backend.as_pandas()

        error.value_check(
            "<WTS98388946E>",
            self.timeseries is not None,
            "Cannot create pandas data frame without any timeseries present",
        )

        error.value_check(
            "<WTS59303952E>",
            self.id_labels is not None,
            "Cannot create pandas data frame without any key labels present",
        )

        key_columns = self.id_labels
        dfs = []
        value_columns = None
        timestamp_column = None
        for ts in self.timeseries:
            if value_columns is None:
                value_columns = ts.value_labels
                if ts.timestamp_label != "":
                    timestamp_column = ts.timestamp_label
            df = ts._get_pd_df()[0]

            for i in range(len(key_columns)):
                id = ts.ids.values[i]
                df[key_columns[i]] = [id for _ in range(df.shape[0])]
            dfs.append(df)
        ignore_index = True  # timestamp_column != ""
        result = pd.concat(dfs, ignore_index=ignore_index)
        self._backend = PandasMultiTimeSeriesBackend(
            result,
            key_column=key_columns,
            timestamp_column=timestamp_column,
            value_columns=value_columns,
        )

        return (
            result,
            key_columns,
            timestamp_column,
            value_columns,
        )

    def as_pandas(self, include_timestamps=None, is_multi=None) -> "pd.DataFrame":
        """Get the view of this timeseries as a pandas DataFrame

        Returns:
            df:  pd.DataFrame
                The view of the data as a pandas DataFrame
        """
        # if as_pandas is_multi is True, and timeseries is_multi is False => add a RESERVED id column with constant value
        # if as_pandas is_multi is True, and timeseries is_multi is True => do nothing just return as is
        # if as_pandas is_multi is False, and timeseries is_multi is True => remove the id columns
        # if as_pandas is_multi is False, and timeseries is_multi is False => do nothing just return as is
        # if as_pandas is_multi is None => do nothing just return as is
        if len(self.id_labels) == 0:
            df = self.timeseries[0].as_pandas(include_timestamps=include_timestamps)

            # add a RESERVED id column with constant value
            if is_multi is not None and is_multi:
                df = df.copy(deep=True)
                df["WATSON_TS_RESERVED"] = np.zeros(len(df), dtype=np.int32)
            return df

        backend_df = self._get_pd_df()[0]
        timestamp_column = self._backend._timestamp_column

        # if we want to include timestamps, but it is not already in the dataframe, we need to add it
        if include_timestamps and timestamp_column is None:
            backend_df = backend_df.copy()  # is this required???
            ts_column = "timestamp"
            backend_df[ts_column] = [0 for _ in range(len(backend_df))]
            backend_df[ts_column] = backend_df.groupby(
                self._backend._key_column, sort=False
            )["timestamp"].transform(lambda x: [i for i in range(len(x))])
            return backend_df
        # if we do not want timestamps, but we already have them in the dataframe, we need to return a view without timestamps
        elif (
            include_timestamps is not None and not include_timestamps
        ) and timestamp_column is not None:
            return backend_df.loc[:, backend_df.columns != timestamp_column]
        else:
            return backend_df

    def as_spark(
        self, include_timestamps=None, is_multi=None
    ) -> "pyspark.sql.DataFrame":
        if not HAVE_PYSPARK:
            raise NotImplementedError("pyspark must be available to use this method.")

        # todo: is this right???
        if len(self.id_labels) == 0:
            df = self.timeseries[0].as_spark(include_timestamps=include_timestamps)
            # add a RESERVED id column with constant value
            if is_multi is not None and is_multi:
                df = df.pandas_api()
                df = df.copy(deep=True)
                df["WATSON_TS_RESERVED"] = np.zeros(len(df), dtype=np.int32).tolist()
                df = df.to_spark()
            return df

        # Third Party
        from pyspark.sql import SparkSession

        # Local
        from ..data_model.backends._spark_backends import SparkMultiTimeSeriesBackend

        # If there is a backend that knows how to do the conversion, use that
        backend = getattr(self, "_backend", None)
        if backend is not None and isinstance(backend, SparkMultiTimeSeriesBackend):
            answer = backend._pyspark_df
            timestamp_column = backend._timestamp_column
            if include_timestamps and timestamp_column is None:

                def append_timestamp_column(aspark_df, key_cols, timestamp_name):
                    sql = f"row_number() OVER (PARTITION BY {','.join(key_cols)} ORDER BY {','.join(key_cols)}) -1 as {timestamp_name}"
                    return aspark_df.selectExpr("*", sql)

                answer = append_timestamp_column(
                    answer, key_cols=self.id_labels, timestamp_name="timestamp"
                )
            elif (
                include_timestamps is not None
                and not include_timestamps
                and timestamp_column is not None
            ):
                answer = answer.drop(timestamp_column)
            return answer
        else:
            pdf = strip_periodic(
                self.as_pandas(include_timestamps=include_timestamps),
                create_copy=True,
            )
            return (
                SparkSession.builder.config(conf=sparkconf_local())
                .getOrCreate()
                .createDataFrame(pdf)
            )
