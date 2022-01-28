"""
Quality tests related to detecting and filtering out data shifts in data
streams.
"""

from pvanalytics.quality import gaps
import numpy as np
import pandas as pd
import ruptures as rpt
import warnings


def _run_data_checks(time_series):
    """
    Check that the passed parameters can be run through the function.
    This includes checking the passed time series, method, cost,
    and penalty values. Throws an error if any of the passed parameter
    types are incorrect.

    Parameters
    ----------
    time_series : Pandas series with datetime index.
        Daily time series of a PV data stream, which can include irradiance
        and power data streams. This series represents the summed daily
        values of the particular data stream.

    Returns
    -------
    None.
    """
    # Check that the time series has a datetime index, and consists of numeric
    # values
    if not isinstance(time_series.index, pd.DatetimeIndex):
        raise TypeError('Must be a Pandas series with a datetime index.')
    # Check that the time series is sampled on a daily basis
    if pd.infer_freq(time_series.index) != "D":
        warnings.warn("Time series frequency not set. Setting frequency to "
                      "daily, and resampling the daily sum value.")
        time_series = time_series.resample('d').sum()
    return


def _erroneous_filter(time_series):
    """
    Remove any outliers from the time series.

    Parameters
    ----------
    time_series : Pandas series with datetime index.
        Daily time series of a PV data stream, which can include irradiance
        and power data streams. This series represents the summed daily values
        of the particular data stream.

    Returns
    -------
    time_series: Pandas series, with a datetime index.
        Time series, after filtering out outliers. This includes removal
        of stale repeat readings, negative readings, and data greater than
        the 99th percentile or less than the 1st percentile.
    """
    # Detect and mask stale data
    stale_mask = gaps.stale_values_round(time_series, window=6,
                                         decimals=3, mark='tail')
    # Mask negative and 0 values
    negative_mask = (time_series <= 0)
    # Mask the top 1% and bottom 1% of data points
    quantile_mask = ((time_series <= time_series.quantile(.01)) |
                     (time_series >= time_series.quantile(.99)))
    # Filter out the associated data by masking
    time_series = time_series[(~stale_mask) & (~negative_mask) &
                              (~quantile_mask)]
    return time_series


def _preprocess_data(time_series, remove_seasonality):
    """
    Pre-process the time series, including the following:
        1. Min-max normalization of the time series.
        2. Removing seasonality from the time series.

    Parameters
    ----------
    time_series : Pandas series with datetime index.
        Daily time series of a PV data stream, which can include irradiance
        and power data streams. This series represents the summed daily values
        of the particular data stream.

    Returns
    -------
    Pandas series with a datetime index:
        Time series, after data processing. This includes min-max
        normalization, and, if the time series is in greater than 2 years in
        length, seasonality removal.
    """
    # Convert the time series to a dataframe to do the pre-processing
    column_name = time_series.name
    if column_name is None:
        column_name = "value"
        time_series = time_series.rename(column_name)
    df = time_series.to_frame()
    # Min-max normalize the series
    df[column_name + "_normalized"] = (df[column_name] -
                                       df[column_name].min())\
        / (df[column_name].max() - df[column_name].min())
    # Check if the time series is greater than one year in length. If not, flag
    # a warning and pass back the normalized time series
    if not remove_seasonality:
        return df[column_name + "_normalized"]
    else:
        # Take the median of every day of the year across all years in the
        # data, and use this as the seasonality of the time series
        df['month'] = pd.DatetimeIndex(df.index).month
        df['day'] = pd.DatetimeIndex(pd.Series(df.index)).day
        df['seasonal_val'] = df.groupby(['month',
                                         'day'])[column_name +
                                                 "_normalized"].transform(
                                                     "median")
        # Remove seasonlity from the time series
        return df[column_name + "_normalized"] - df['seasonal_val']


def detect_data_shifts(time_series, filtering=True, use_default_models=True,
                       method=rpt.BottomUp, cost='rbf', penalty=40):
    """
    Detect data shifts in the time series, and return list of dates where these
    data shifts occur.

    Parameters
    ----------
    time_series : Pandas series with datetime index.
        Daily time series of a PV data stream, which can include irradiance
        and power data streams. This series represents the summed daily values
        of the particular data stream.
    filtering : Boolean, default True.
        Whether or not to filter out outliers and stale data from the time
        series. If True, then this data is filtered out before running the
        data shift detection sequence. If False, this data is not filtered
        out. Default set to True.
    use_default_models: Boolean, default True
        If True, then default change point detection search parameters are
        used. For time series shorter than 2 years in length, the search
        function is `rpt.Window`  with `model='rbf'`, `width=40` and
        `penalty=30`. For time series 2 years or longer in length, the
        search function is `rpt.BottomUp` with `model='rbf'`
        and `penalty=40`.
    method: ruptures search method instance or None, default None.
        Ruptures search method instance. See
        https://centre-borelli.github.io/ruptures-docs/user-guide/.
    cost: str or None, default None
        Cost function passed to the ruptures changepoint search instance.
        See https://centre-borelli.github.io/ruptures-docs/user-guide/
    penalty: numeric (float or int)
        Penalty value passed to the ruptures changepoint detection method.
        Default set to 40.

    Returns
    -------
    Pandas Series
        Series of boolean values with a datetime index, where detected
        changepoints are labeled as True, and all other values are labeled
        as False.

    References
    -------
    .. [1] Perry K., and Muller, M. "Automated shift detection in sensor-based
       PV power and irradiance time series", 2022 IEEE 48th Photovoltaic
       Specialists Conference (PVSC). Submitted.
    """
    # Run data checks on cleaned data to make sure that the data can be run
    # successfully through the routine
    _run_data_checks(time_series)
    # Run the filtering sequence, if marked as True
    if filtering:
        time_series = _erroneous_filter(time_series)
    # Drop any duplicated data from the time series
    time_series = time_series.drop_duplicates()
    # Check if the time series is more than 2 years long. If so, remove
    # seasonality. If not, run analysis on the normalized time series
    if (time_series.index.max() - time_series.index.min()).days <= 730:
        warnings.warn("The passed time series is less than 2 years in length, "
                      "and will not be corrected for seasonality. Runnning "
                      "data shift detection on the min-max normalized time "
                      "series with no seasonality correction.")
        time_series_processed = _preprocess_data(time_series,
                                                 remove_seasonality=False)
        seasonality_rmv = False
    else:
        # Perform pre-processing on the time series, to get the
        # seasonality-removed time series.
        time_series_processed = _preprocess_data(time_series,
                                                 remove_seasonality=True)
        seasonality_rmv = True
    points = np.array(time_series_processed.dropna())
    # If seasonality has been removed and default model is used, run
    # BottomUp method
    if (seasonality_rmv) & (use_default_models):
        algo = rpt.BottomUp(model='rbf').fit(points)
        result = algo.predict(pen=40)
    # If there is no seasonality but default model is used, run
    # Window-based method
    elif (not seasonality_rmv) & (use_default_models):
        algo = rpt.Window(model='rbf',
                          width=50).fit(points)
        result = algo.predict(pen=30)
    # Otherwise run changepoint detection with the passed parameters
    else:
        algo = method(model=cost).fit(points)
        result = algo.predict(pen=penalty)
    # Remove the last index of the time series, if present
    if len(points) in result:
        result.remove(len(points))
    # Return a list of dates where changepoints are detected
    time_series_processed.index.name = "datetime"
    time_series_processed = time_series_processed.reset_index()
    time_series_processed['cpd_mask'] = time_series_processed.index.isin(
        result)
    time_series_processed.index = time_series_processed['datetime']
    return time_series_processed['cpd_mask']


def get_longest_shift_segment_dates(time_series, filtering=True,
                                    use_default_models=True,
                                    method=rpt.BottomUp, cost="rbf",
                                    penalty=40):
    """
    Return the start and end dates of the longest continuous time series
    segment. During this process, data shift detection is performed, and the
    longest time series segment between changepoints is identified, and the
    start and end dates of that segment are returned.

    Parameters
    ----------
    time_series : Pandas series with datetime index.
        Daily time series of a PV data stream, which can include irradiance
        and power data streams. This series represents the summed daily values
        of the particular data stream.
    filtering : Boolean.
        Whether or not to filter out outliers and stale data from the time
        series. If True, then this data is filtered out before running the
        data shift detection sequence. If False, this data is not filtered
        out. Default set to True.
    use_default_models: Boolean.
        If set to True, then default CPD model parameters are used, based
        on the length of the time series (Window-based models for time series
        shorter than 2 years in length and BottomUp models for time series
        longer than 2 years in length). If set to True, none of the method +
        cost + penalty variables are used.
    method: ruptures search method object.
        Ruptures method object. Can be one of the following methods:
        ruptures.Pelt, ruptures.Binseg, ruptures.BottomUp, or ruptures.Window.
        See the following documentation for further information:
        https://centre-borelli.github.io/ruptures-docs/user-guide/. Default set
        to ruptures.BottomUp search method.
    cost: str
        Cost function passed to the ruptures changepoint detection method. Can
        be one of the following string values: 'rbf', 'l1', 'l2', 'normal',
        'cosine', or 'linear'. See the following documentation for further
        information: https://centre-borelli.github.io/ruptures-docs/user-guide/
        Default set to "rbf".
    penalty: numeric (float or int)
        Penalty value passed to the ruptures changepoint detection method.
        Default set to 40.

    Returns
    -------
    passing_dates_dict: Dictionary.
        Dictionary object containing the longest continuous time segment
        with no detected data shifts. The start date of the period is
        represented in the "start_date" field, and the end date of the
        period is represented in the "end_date" field.

    References
    -------
    .. [1] Perry K., and Muller, M. "Automated shift detection in sensor-based
       PV power and irradiance time series", 2022 IEEE 48th Photovoltaic
       Specialists Conference (PVSC). Submitted.
    """
    # Detect indices where data shifts occur
    cpd_mask = detect_data_shifts(time_series, filtering,
                                  use_default_models,
                                  method, cost, penalty)
    # Get longest continuous data segment by number of days in the time series
    if all(~cpd_mask):
        passing_dates_dict = {"start_date": time_series.index.min(),
                              "end_date": time_series.index.max()
                              }
        return passing_dates_dict
    # Add the start and end dates in the sequence, and remove any
    # duplications. Finally, sort in order of timestamp, from oldest to
    # newest.
    data_shift_dates = list(cpd_mask[cpd_mask].index)
    data_shift_dates.append(time_series.index.min())
    data_shift_dates.append(time_series.index.max())
    data_shift_dates = list(set(data_shift_dates))
    data_shift_dates.sort()
    # Find the longest date segment in the time series, with the most data
    # points.
    segment_lengths = [len(time_series[data_shift_dates[i]:
                                       data_shift_dates[i+1]])
                       for i in range(len(data_shift_dates)-1)]
    max_segment_length_idx = segment_lengths.index(max(segment_lengths))
    passing_dates_dict = {"start_date":
                          data_shift_dates[max_segment_length_idx],
                          "end_date":
                              data_shift_dates[max_segment_length_idx + 1]
                          }
    return passing_dates_dict
