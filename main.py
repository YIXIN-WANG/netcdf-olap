import os
import pdb
import json
from flask import send_file
from subprocess import call as proccall
from flask import Flask
from flask import request
from flask_cors import CORS
from flask import jsonify
from flask import send_file
import zlib
import zipfile

# from geoPy import geopy


app = Flask("NetCDF-Server")
CORS(app)
basedir = os.path.abspath(os.path.dirname(__file__))

def parse_json(obj):
    daterange = obj["selectDate"]
    variables = obj["variables"]
    geo = obj["geoJson"]

    all_coordinates = geo["coordinates"][0]
    geo_type = geo["type"]
    top_left = all_coordinates[0]
    top_right = all_coordinates[1]
    bottom_right = all_coordinates[2]
    bottom_left = all_coordinates[3]
    center = all_coordinates[4]

    top_left_lon = top_left[0]
    bottom_left_lon = bottom_left[0]

    top_right_lon = top_right[0]
    bottom_right_lon = bottom_right[0]

    min_lon = min(top_left_lon, bottom_left_lon)
    max_lon = max(top_right_lon, bottom_right_lon)

    top_left_lat = top_left[1]
    bottom_left_lat = bottom_left[1]

    top_right_lat = top_right[1]
    bottom_right_lat = bottom_right[1]

    min_lat = min(bottom_right_lat, bottom_left_lat)
    max_lat = max(top_right_lat, top_left_lat)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": geo_type,
                    "coordinates": [
                        [
                            [
                                top_left_lon,
                                top_left_lat
                            ],
                            [
                                top_right_lon,
                                top_right_lat
                            ],
                            [
                                bottom_right_lon,
                                bottom_right_lat
                            ],
                            [
                                bottom_left_lon,
                                bottom_left_lat
                            ],
                            [
                                center[0],
                                center[1]
                            ]
                        ]
                    ]
                }
            }
        ]
    }
    with open("geojson.json", 'w') as f:
        f.write(json.dumps(geojson))

    return (daterange, variables)
    # return [min_lat, max_lat, min_lon, max_lon]


@app.route('/getBoundary', methods=['GET'])
def getBoundary():
    boundary = [[41.0270393371582, -93.11832427978516], [49.424442291259766, -75.28765258789062]]
    return jsonify(boundary)


@app.route('/fetchResult', methods=['POST'])
def fetchResult():
    daterange, variables = parse_json(request.get_json())
    # Calling the Python code is commented out
    # geopy.process_query(data[0], data[1], data[2], data[3])
    print(request.get_json())
    if daterange:
        proccall(
            "spark-submit --master 'local[*]' gddp/target/scala-2.11/gddp-assembly-0.22.7.jar data geojson.json "
            + daterange + " " + variables,
            shell=True
        )
        # process completed.

        rv = None
        compression = zipfile.ZIP_DEFLATED
        zf = zipfile.ZipFile("result.zip", mode="w")
        try:
            for v in variables.split(","):
                zf.write('gddp' + v + daterange.replace(',', '-') + '.png', compress_type=compression)


        except FileNotFoundError:
            print("No files generated")
        finally:
            # Don't forget to close the file!
            zf.close()
            rv = send_file("result.zip", mimetype='application/zip')
            return rv
            # Use the below line if you want to return the result of Python code.
            # return send_file('result.txt', mimetype='text/csv')

    else:
        return '{message: "Server Error"}'

if __name__ == '__main__':
    app.run()
