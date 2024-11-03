import os
import logging
import numpy as np
import dask.dataframe as dd
from dask.diagnostics import ProgressBar
import pyarrow as pa
from dask_ml.preprocessing import DummyEncoder
import pandas as pd

def load_and_preprocess_data(train_folder, filters, output_file_path, exclude_columns, delimiter=';'):
    """Load and preprocess data from the specified folder."""
    logging.info(f"Starting to load data from {train_folder}")
    
    data_files = _get_csv_files(train_folder)
    logging.info(f"Found {len(data_files)} CSV files to process.")
    
    data = _load_data(data_files, delimiter)
    if data is None:
        return None
    
    return preprocess_and_save_data(data, filters, exclude_columns, output_file_path)

def _get_csv_files(folder):
    """Get a list of CSV files in the specified folder."""
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.csv')]

def _load_data(data_files, delimiter):
    """Load data from CSV files into a Dask DataFrame."""
    try:
        with ProgressBar():
            logging.info("Loading data into Dask DataFrame.")
            data = dd.read_csv(data_files, delimiter=delimiter, dtype={
                'LINIEN_ID': 'object', 
                'UMLAUF_ID': 'object',
                'BETREIBER_ABK': 'object',
                'LINIEN_TEXT': 'object',
                'BETRIEBSTAG': 'object'
            }, low_memory=False)
        logging.info("Data loaded into Dask DataFrame.")
        logging.info(f"Data columns: {data.columns}")
        return data
    except Exception as e:
        logging.error(f"Error loading data into Dask DataFrame: {e}")
        return None

def _exclude_columns(data, exclude_columns):
    """Exclude specified columns from the data."""
    try:
        logging.info(f"Excluding columns: {exclude_columns}")
        data = data.drop(columns=exclude_columns, errors='ignore')
        logging.info(f"Columns after exclusion: {data.columns}")
        return data
    except Exception as e:
        logging.error(f"Error excluding columns: {e}")
        return None

def _encode_categorical_columns(data):
    """Encode all columns to int64 using Dask's parallel processing."""
    try:
        logging.info("Encoding all columns to int64.")
        
        def encode_partition(df):
            for column in df.columns:
                df[column] = df[column].astype('category').cat.codes
                df[column] = df[column].astype('int64')
            return df
        
        data = data.map_partitions(encode_partition)
        logging.info("All columns encoded to int64.")
    except Exception as e:
        logging.error(f"Error encoding columns: {e}")
        return None
    return data

def preprocess_and_save_data(data, filters, exclude_columns, output_file_path):
    """Preprocess and save data to a Parquet file."""
    data = _apply_filters(data, filters)
    if data is None:
        return None
    
    data = _exclude_columns(data, exclude_columns)
    if data is None:
        return None
    
    data = _preprocess_data(data)
    if data is None:
        return None
    
    def calculate_time_difference(data, col1, col2, new_col_name):
        try:
            data[col1] = dd.to_datetime(data[col1], format='mixed', dayfirst=True)
            data[col2] = dd.to_datetime(data[col2], format='mixed', dayfirst=True)
            data[new_col_name] = (data[col1] - data[col2]).dt.total_seconds()
        except Exception as e:
            logging.error(f"Error calculating time difference for {new_col_name}: {e}")
            return None
        return data
    
    data = calculate_time_difference(data, 'ANKUNFTSZEIT', 'AN_PROGNOSE', 'ARRIVAL_TIME_DIFF_SECONDS')
    if data is None:
        return None
    
    data = calculate_time_difference(data, 'ABFAHRTSZEIT', 'AB_PROGNOSE', 'DEPARTURE_TIME_DIFF_SECONDS')
    if data is None:
        return None
    
    data = _encode_categorical_columns(data)
    if data is None:
        return None
    
    return save_processed_data(data, output_file_path)

def _apply_filters(data, filters):
    """Apply filters to the data."""
    try:
        logging.info("Applying custom filters for AN_PROGNOSE_STATUS and AB_PROGNOSE_STATUS.")
        with ProgressBar():
            data = data[(data["AN_PROGNOSE_STATUS"] == "REAL") & (data["AB_PROGNOSE_STATUS"] == "REAL")]
        logging.info("Custom filters applied.")
    except Exception as e:
        logging.error(f"Error applying custom filters: {e}")
        return None

    if filters:
        for column, values in filters.items():
            if not isinstance(values, list):
                values = [values]
            logging.info(f"Applying filter: {column} in {values}")
            try:
                if column in data.columns:
                    with ProgressBar():
                        data = data[data[column].isin(values)]
                    logging.info(f"Filter applied on column: {column}")
                else:
                    logging.error(f"Column {column} does not exist in the data.")
                    return None
            except Exception as e:
                logging.error(f"Error applying filter on column {column}: {e}")
                return None
    return data

def _preprocess_data(data):
    """Preprocess the data by filling missing values and inferring object types."""
    try:
        logging.info("Filling missing values with NaN.")
        with ProgressBar():
            data = data.fillna(np.nan)
            data = data.map_partitions(lambda df: df.infer_objects(copy=False))
        logging.info("Missing values filled with NaN and inferred object types.")
        return data
    except Exception as e:
        logging.error(f"Error during data preprocessing: {e}")
        return None

def save_processed_data(data, output_file_path):
    """
    Save the processed data to a Parquet file.

    Parameters:
    data (dask.dataframe.DataFrame): The processed data to be saved.
    output_file_path (str): The file path where the data should be saved.

    Returns:
    str: The path to the saved Parquet file, or None if an error occurred.
    """
    try:
        logging.info(f"Starting to save processed data to {output_file_path}")
        
        with ProgressBar():
            logging.info("Repartitioning data into 20 partitions")
            data = data.repartition(npartitions=20)
            
            # Create dynamic schema based on the encoded columns
            logging.info("Creating dynamic schema based on the encoded columns")
            column_types = data.dtypes
            schema_fields = []
            for col, dtype in column_types.items():
                if pd.api.types.is_bool_dtype(dtype):
                    pa_type = pa.bool_()
                elif pd.api.types.is_integer_dtype(dtype):
                    pa_type = pa.int64()
                elif pd.api.types.is_float_dtype(dtype):
                    pa_type = pa.float64()
                else:
                    pa_type = pa.string()
                schema_fields.append((col, pa_type))
                logging.debug(f"Column: {col}, Type: {dtype}, Parquet Type: {pa_type}")
            
            schema = pa.schema(schema_fields)
            logging.info("Schema creation complete")
            
            output_file_path = output_file_path.replace('.csv', '.parquet')
            logging.info(f"Output file path changed to {output_file_path}")
            
            logging.info("Starting to write data to Parquet file")
            data.to_parquet(output_file_path, 
                            engine='pyarrow', 
                            compression='snappy', 
                            schema=schema, 
                            write_metadata_file=False)
            
        logging.info("Successfully processed and saved data to a Parquet file.")
        return output_file_path
    except Exception as e:
        logging.error(f"Error saving processed data to Parquet file: {e}")
        return None
