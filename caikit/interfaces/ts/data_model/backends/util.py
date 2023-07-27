"""Internal utilities for supporting backend implementations"""

# Standard
from datetime import datetime
from typing import Any, Iterable, List, Union

# Third Party
import numpy as np
import pandas as pd

# Local
from ...data_model.toolkit.optional_dependencies import HAVE_PYSPARK, pyspark


def mock_pd_groupby(a_df_like, by: List[str], return_pandas_api=False):
    """Roughly mocks the behavior of pandas groupBy but on a spark dataframe."""

    distinct_keys = a_df_like.select(by).distinct().collect()
    for dkey in distinct_keys:
        adict = dkey.asDict()
        filter_statement = ""
        for k, v in adict.items():
            filter_statement += f" {k} == '{v}' and"
        if filter_statement.endswith("and"):
            filter_statement = filter_statement[0:-3]
        sub_df = a_df_like.filter(filter_statement)
        value = tuple(adict.values())
        value = value[0] if len(value) == 1 else value
        yield value, sub_df.pandas_api() if return_pandas_api else sub_df


def timezoneoffset(adatetime: datetime) -> int:
    """Returns the timezone offset (in seconds)
    for a given datetime object relative to the local
    system's time.

    Args:
        adatetime (datetime): a date of interest.

    Returns:
        int: offset in seconds (can be negative)
    """
    return (
        adatetime.timestamp()
        - datetime(
            year=adatetime.year,
            month=adatetime.month,
            day=adatetime.day,
            hour=adatetime.hour,
            minute=adatetime.minute,
            second=adatetime.second,
            microsecond=adatetime.microsecond,
        ).timestamp()
    )


def pd_timestamp_to_seconds(ts) -> float:
    """Extract the seconds-since-epoch representation of the timestamp

    NOTE: The pandas Timestamp.timestamp() function returns a different value
        than Timestamp.to_pydatetime().timestamp()! Since we want this to
        round-trip with python datetime, we want the latter. They both claim to
        be POSIX, so something is missing leap-something!
    """
    if isinstance(ts, pd.Period):
        return ts.to_timestamp().timestamp()  # no utc shift
    elif isinstance(ts, np.datetime64):
        return ts.astype("datetime64[ns]").astype(float) / 1e9
    elif isinstance(ts, datetime):
        return ts.timestamp()
    elif isinstance(ts, (int, float, np.int32, np.int64, np.float32, np.float64)):
        return float(ts)
    else:
        raise ValueError(f"invalid type {type(ts)} for parameter ts.")


def strip_periodic(
    input_df: pd.DataFrame, ts_col_name: Union[str, None] = None, create_copy=True
) -> pd.DataFrame:
    """
    Removes **the first instance** of a periodic timestamp info
    (because spark doesn't like these when constructing a pyspark.sql.DataFrame.)
    If no periodic timestamp values can be found, input_df is returned as is.
    This method is always a no-op if input_df is not a native pandas.DataFrame.
    """

    if not isinstance(input_df, pd.DataFrame):
        return input_df

    # find location of period field
    try:
        index = (
            [type(x) for x in input_df.dtypes].index(pd.core.dtypes.dtypes.PeriodDtype)
            if ts_col_name is None
            else input_df.columns.to_list().index(ts_col_name)
        )
    except ValueError:
        index = -1

    df = input_df
    if index >= 0:
        df = input_df if not create_copy else input_df.copy(deep=False)
        df.iloc[:, index] = [
            x.to_timestamp() if hasattr(x, "to_timestamp") else x
            for x in df.iloc[:, index]
        ]

    return df


def iteritems_workaround(series: Any, force_list: bool = False) -> Iterable:
    """pyspark.pandas.Series objects do not support
    iteration. For native pandas.Series objects this
    function will be a no-op.

    For pyspark.pandas.Series or other iterable objects
    we try to_numpy() (unless force_list
    is true) and if that fails we resort to a to_list()

    """

    # python just stinks!
    if not hasattr(series, "to_list") and not hasattr(series, "to_numpy"):
        raise NotImplementedError(
            f"invalid typed {type(series)} passed for parameter series"
        )

    if isinstance(series, pd.Series):
        return series

    # handle an edge case of pyspark.ml.linalg.DenseVector
    if HAVE_PYSPARK:
        if isinstance(series, pyspark.pandas.series.Series) and isinstance(
            series[0], pyspark.ml.linalg.DenseVector
        ):
            return [x.values.tolist() for x in series.to_numpy()]


    # note that we're forcing a list only if we're not
    # a native pandas series
    if force_list:
        return series.to_list()

    try:
        return series.to_numpy()
    except:
        return series.to_list()