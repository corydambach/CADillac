#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Populate collections_v1.db with model data from JSON file.

This script reads a JSON file containing vehicle model data and populates
a SQLite database with the information, creating the necessary tables if
they don't exist.
"""

import os
import os.path as op
import sqlite3
import argparse
import logging
import json
from pprint import pformat


def maybeCreateTableCad(cursor):
    """Create the 'cad' table if it doesn't exist."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cad (
            model_id TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            id TEXT,
            model_name TEXT,
            description TEXT,
            issue TEXT,
            car_make TEXT,
            car_year TEXT,
            car_model TEXT,
            dims_L REAL,
            dims_W REAL,
            dims_H REAL,
            color TEXT,
            domain TEXT,
            type1 TEXT,
            url TEXT,
            error TEXT,
            comment TEXT,
            PRIMARY KEY (model_id, collection_id)
        )
    ''')
    logging.info('Table "cad" created or already exists.')


def maybeCreateTableClas(cursor):
    """Create the 'clas' table if it doesn't exist."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clas (
            label TEXT,
            class TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            PRIMARY KEY (class, collection_id, model_id),
            FOREIGN KEY (model_id, collection_id) REFERENCES cad(model_id, collection_id)
        )
    ''')
    logging.info('Table "clas" created or already exists.')


def getAllCadColumns():
    """Return all column names for the cad table in order."""
    return [
        'model_id',
        'collection_id',
        'id',
        'model_name',
        'description',
        'issue',
        'car_make',
        'car_year',
        'car_model',
        'dims_L',
        'dims_W',
        'dims_H',
        'color',
        'domain',
        'type1',
        'url',
        'error',
        'comment'
    ]


def importModelsFromJson(cursor, json_path, overwrite=False):
    """
    Import models from a JSON file into the database.
    
    Args:
        cursor: SQLite cursor
        json_path: Path to the JSON file
        overwrite: If True, update existing entries; if False, skip them
    """
    # Load JSON data
    if not op.exists(json_path):
        raise FileNotFoundError(f'JSON file not found: {json_path}')
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    models = data.get('models', [])
    logging.info(f'Found {len(models)} models in the JSON file')
    
    cols = getAllCadColumns()
    
    for model in models:
        try:
            # Map JSON fields to database columns
            # Handle dimension fields that come as separate fields in JSON
            if 'length' in model:
                model['dims_L'] = float(model['length']) if model['length'] else None
            if 'width' in model:
                model['dims_W'] = float(model['width']) if model['width'] else None
            if 'height' in model:
                model['dims_H'] = float(model['height']) if model['height'] else None
            
            # Map 'type' to 'type1' to match database schema
            if 'type' in model:
                model['type1'] = model['type']
            
            # Convenience variable
            modelcol = (model['model_id'], model['collection_id'])
            
            # Check if the model is already in this collection
            cursor.execute('SELECT COUNT(1) FROM cad WHERE model_id=? AND collection_id=?', modelcol)
            num_present = cursor.fetchone()[0]
            assert num_present <= 1, f'Primary key restriction violated: {model["model_id"]}'
            
            # Check if there is already such a model in a different collection
            cursor.execute('SELECT collection_id FROM cad WHERE model_id=? AND collection_id!=?', modelcol)
            other_collection_ids = cursor.fetchall()
            if len(other_collection_ids) >= 1:
                logging.warning(f'Model {model["model_id"]} already exists in collection(s) {other_collection_ids}')
            
            if num_present == 1 and not overwrite:
                logging.warning(f'Skipping model {modelcol} because it already exists.')
                continue
            
            elif num_present == 0:
                logging.info(f'Inserting model {model["model_id"]} from collection {model["collection_id"]}')
                s = f'INSERT INTO cad({",".join(cols)}) VALUES ({",".join(["?"] * len(cols))})'
            
            elif num_present == 1 and overwrite:
                logging.info(f'Updating model {model["model_id"]} from collection {model["collection_id"]}')
                s = f'UPDATE cad SET {",".join([f"{c}=?" for c in cols[2:]])} WHERE model_id=? AND collection_id=?'
                cols_temp = cols[2:] + cols[:2]  # model_id and collection_id are moved to the end
            else:
                continue
            
            logging.debug(f'Will execute: {s}')
            
            # Form an entry of values, valid for both INSERT and UPDATE
            if num_present == 0:
                entry = tuple([model.get(name) for name in cols])
            else:  # UPDATE case
                entry = tuple([model.get(name) for name in cols_temp])
            
            logging.debug(str(entry))
            cursor.execute(s, entry)
            
        except Exception as e:
            logging.error(f'Error occurred processing model:\n{pformat(model)}')
            logging.error(f'Error: {str(e)}')
            raise


def main():
    parser = argparse.ArgumentParser(
        description='Populate collections_v1.db with model data from JSON file.'
    )
    parser.add_argument('--json_file', required=True, help='Path to JSON file with model data')
    parser.add_argument('--db_file', default='collections_v1.db', help='Path to SQLite database file')
    parser.add_argument('--overwrite', action='store_true', 
                        help='Overwrite existing entries')
    parser.add_argument('--logging', type=int, default=20, 
                        help='Logging level (10=DEBUG, 20=INFO, 30=WARNING)')
    parser.add_argument('--dry_run', action='store_true',
                        help='Do not commit changes (for testing)')
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=args.logging,
        format='%(levelname)s: %(message)s'
    )
    
    # Connect to database
    logging.info(f'Connecting to database: {args.db_file}')
    conn = sqlite3.connect(args.db_file)
    cursor = conn.cursor()
    
    try:
        # Create tables if they don't exist
        maybeCreateTableCad(cursor)
        maybeCreateTableClas(cursor)
        
        # Import models from JSON
        importModelsFromJson(cursor, args.json_file, args.overwrite)
        
        # Commit changes unless dry_run
        if not args.dry_run:
            logging.info('Committing changes to database.')
            conn.commit()
        else:
            logging.info('Dry run mode: changes not committed.')
        
        # Show statistics
        cursor.execute('SELECT COUNT(*) FROM cad')
        count = cursor.fetchone()[0]
        logging.info(f'Database now contains {count} models.')
        
    except Exception as e:
        logging.error(f'Error during import: {str(e)}')
        conn.rollback()
        raise
    
    finally:
        conn.close()
        logging.info('Database connection closed.')


if __name__ == '__main__':
    main()