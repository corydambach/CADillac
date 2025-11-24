# White Ford.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  --clause 'INNER JOIN clas c1 ON cad.model_id=c1.model_id WHERE c1.label = "white" AND cad.car_make == "ford" AND error IS NULL' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_white_ford.png

# Van.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections.db `
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "van" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_van.png

# Toyota truck.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE cad.car_make == "toyota" AND clas.label = "truck" AND error IS NULL' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_toyota_truck.png

# Military.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections.db `
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "military" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_military.png

# Fiction.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections.db `
  --clause 'INNER JOIN clas ON cad.model_id=clas.model_id WHERE clas.label = "fiction" AND cad.model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_fiction.png

# Cars longer than X1 and shorter than X2 (on collecton_v1).
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  --clause 'WHERE dims_L >= 9 AND dims_L <= 10 AND error ISNULL' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_length_9_to_10.png `
  --at_most 8

# Error: matte glass
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  --clause 'WHERE error == "matte glass"' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_error_matte_glass.png `
  --at_most 8

# Error: triangles
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  --clause 'WHERE error == "triangles"' `
  makeGrid `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_error_triangles.png `
  --at_most 8

# Histogram of lengths (on collection_v1).
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections_v1.db `
  plotHistogram `
  --query 'SELECT dims_L FROM cad WHERE error ISNULL AND dims_L <= 25' `
  --xlabel 'length, m' --ylog `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_length.eps

# Histogram of car makes which have at least 5 models.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections.db `
  plotHistogram `
  --query 'SELECT car_make FROM cad WHERE car_make IN (SELECT car_make FROM cad GROUP BY car_make HAVING COUNT(car_make) >= 5) AND model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' `
  --categorical `
  --rotate_xticklabels `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_make_ge5.eps

# Histogram of car types1.
python3 cads/Modify.py `
  --in_db_file $env:CADILLAC_DATA_PATH/CAD/collections.db `
  plotHistogram `
  --query 'SELECT label FROM clas WHERE class="type1" AND model_id NOT IN (SELECT model_id FROM clas WHERE class == "issue")' `
  --categorical `
  --out_path $env:CADILLAC_DATA_PATH/CAD/-visualizations/v1_hist_type1.eps