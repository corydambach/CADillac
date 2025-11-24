```
export CADILLAC_DATA_PATH=$CITY_PATH/data/augmentation
PATCHDIR=$CADILLAC_DATA_PATH/test
```

Augmentation
============

## To make patches:

```bash
# Render.
python3 render/MakePatches.py \
  -o $PATCHDIR/scenes.db \
  --num_sessions 10 --num_per_session 3 --num_occluding 1 2 3 4 5 --mode PARALLEL \
  --clause_main 'WHERE error IS NULL AND dims_L < 10' \
  --cad_db_path $CADILLAC_DATA_PATH/CAD/collections_v1.db

# Copy CAD info.
python3 render/CopyCadProperties.py \
  --in_db_path  $PATCHDIR/scenes.db \
  --out_db_path $PATCHDIR/scenes-filled.db \
  --cad_db_path $CADILLAC_DATA_PATH/CAD/collections_v1.db

# Crop.
python3 ~/projects/shuffler/shuffler.py \
  --rootdir $PATCHDIR \
  -i $PATCHDIR/scenes-filled.db \
  -o $PATCHDIR/patches-e02-w64.db \
  filterObjectsAtBorder   \| \
  expandBoxes --expand_perc 0.2   \| \
  cropObjects --edges distort --target_width 64 --target_height 64 --media video  \
    --image_path $PATCHDIR/patches-e02-w64.avi \
    --mask_path $PATCHDIR/patches-e02-w64mask.avi

# Write how much background is seen.
python3 render/RecordInfiniteBack.py \
  --in_db_path  $PATCHDIR/patches-e02-w64.db \
  --out_db_path $PATCHDIR/patches-e02-w64.db \
  --rootdir $CADILLAC_DATA_PATH/test

# Hide background from mask.
python3 ~/projects/shuffler/shuffler.py \
  --rootdir $PATCHDIR \
  -i $PATCHDIR/patches-e02-w64.db \
  -o $PATCHDIR/patches-e02-w64-repainted.db \
  repaintMask --media video \
    --mask_path $PATCHDIR/patches-e02-w64mask-noback.avi \
    --mask_mapping_dict "{255: 255, 0: 0, 128: 0}" --overwrite

# From this point on, no expensive image operations.
cp $PATCHDIR/patches-repainted.db $PATCHDIR/patches.db

# Count images where background is more than 5% (returns 779)
sqlite3 $PATCHDIR/patches.db "SELECT COUNT(1) FROM properties WHERE key = 'background' AND CAST(value AS REAL) > 0.05"
# Remove them.
sqlite3 $PATCHDIR/patches.db "DELETE FROM objects WHERE objectid IN (SELECT objectid FROM properties WHERE key = 'background' AND CAST(value AS REAL) > 0.05)"
sqlite3 $PATCHDIR/patches.db "DELETE FROM properties WHERE objectid IN (SELECT objectid FROM properties WHERE key = 'background' AND CAST(value AS REAL) > 0.05)"

# Filter low visibility
sqlite3 $PATCHDIR/patches.db "DELETE FROM properties WHERE objectid IN (SELECT objectid FROM objects WHERE score < 0.7)"
sqlite3 $PATCHDIR/patches.db "DELETE FROM objects WHERE score < 0.7"

# Visibility to properties
sqlite3 $PATCHDIR/patches.db "INSERT INTO properties(objectid,key,value) SELECT objectid,'visibility',score FROM objects"
sqlite3 $PATCHDIR/patches.db "UPDATE objects SET score=NULL"

# Remove images associated with no objects.
python3 ~/projects/shuffler/shuffler.py \
  --rootdir $PATCHDIR \
  -i $PATCHDIR/patches.db \
  -o $PATCHDIR/patches.db \
  filterEmptyImages
```

```bash
python src/augmentation/ProcessFrame.py --video_dir augmentation/scenes/cam572/Nov28-10h
```

```bash
python src/augmentation/GenerateTraffic.py  --job_file augmentation/jobs/572-Feb23-09h-test.json --traffic_file augmentation/video/test/traffic.json
```

```bash
python src/augmentation/ProcessVideo.py --timeout 10 --job_file augmentation/jobs/572-Feb23-09h-test.json --traffic_file augmentation/video/test/traffic.json
```

&&&

```bash
export CADILLAC_DATA_PATH=$CITY_PATH/data/augmentation
```


Scripts:

```bash
collection_id=\'5f08583b1f45a9a7c7193c87bbfa9088\'  # Quotes are important in "clause" arg.

# Import collections.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  importCollections \
  --collection_ids ${collection_id}

# Classify color.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  --out_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  --clause "collection_id=${collection_id}" \
  --class_name=color --key_dict_json='{"w": "white", "k": "black", "e": "gray", "r": "red", "y": "yellow", "g": "green", "b": "blue", "o": "orange"}'

# Correct model_name.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  manuallyEditCarModel \
  --car_query_db_path $CADILLAC_DATA_PATH/resources/CQA_Advanced_v1.db
```

SQL queries:

```sql
# Display the number of cars of each color.
SELECT label, COUNT(1) FROM clas WHERE class='color' GROUP BY label ORDER BY COUNT(1) DESC

# Display the number in each collection.
SELECT collection_id, COUNT(1) FROM cad GROUP BY collection_id ORDER BY COUNT(1) DESC

# Display car_make with its count.
SELECT car_make, COUNT(1) FROM cad GROUP BY car_make ORDER BY COUNT(1) DESC

# Copy issue to error field.
UPDATE cad SET error = (SELECT clas.label FROM clas WHERE clas.model_id == cad.model_id AND clas.collection_id == cad.collection_id AND clas.class == 'issue') WHERE EXISTS (SELECT * FROM clas WHERE clas.model_id == cad.model_id AND clas.collection_id == cad.collection_id AND clas.class == 'issue')
```


Visualization:
```bash
# White Ford.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'INNER JOIN clas c1 ON cad.model_id=c1.model_id WHERE c1.label = "white" AND cad.car_make == "ford" AND error IS NULL' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_white_ford.png

# Van.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "van" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_van.png

# Toyota truck.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE cad.car_make == "toyota" AND clas.label = "truck" AND error IS NULL' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_toyota_truck.png

# Military.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "military" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_military.png

# Fiction.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "fiction" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_fiction.png

# Cars longer than X1 and shorter than X2 (on collecton_v1).
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'WHERE dims_L >= 9 AND dims_L <= 10 AND error ISNULL' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_length_9_to_10.png \
  --at_most 8

% Error: matte glass
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'WHERE error == "matte glass"' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_error_matte_glass.png \
  --at_most 8

% Error: triangles
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'WHERE error == "triangles"' \
  makeGrid \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_error_triangles.png \
  --at_most 8

# Histogram of lengths (on collection_v1).
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  plotHistogram \
  --query 'SELECT dims_L FROM cad WHERE error ISNULL AND dims_L <= 25' \
  --xlabel 'length, m' --ylog \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_length.eps

# Histogram of car makes which have at least 5 models.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  plotHistogram \
  --query 'SELECT car_make FROM cad WHERE car_make IN (SELECT car_make FROM cad GROUP BY car_make HAVING COUNT(car_make) >= 5) AND model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' \
  --categorical \
  --rotate_xticklabels \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_make_ge5.eps

# Histogram of car types1.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections.db \
  plotHistogram \
  --query 'SELECT label FROM clas WHERE class="type1" AND model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' \
  --categorical \
  --out_path $CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_type1.eps
```


## Work with CarQueryDb

```bash
# Create a db.
python3 cads/MakeCarQueryDb.py \
  --out_db_file $CADILLAC_DATA_PATH/resources/CQA_Advanced_v1.1.db

# Look up how many models are in this CarQueryDb.
python3 cads/Modify.py \
  --in_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --out_db_file $CADILLAC_DATA_PATH/CAD/collections_v1.db \
  --clause 'WHERE car_make IS NOT NULL AND car_model IS NOT NULL AND comment IS NULL' \
  fillDimsFromCarQueryDb \
  --car_query_db_path $CADILLAC_DATA_PATH/resources/CQA_Advanced_v1.1.db
```
