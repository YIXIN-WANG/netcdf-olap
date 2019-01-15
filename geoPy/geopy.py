from os import listdir
from os.path import isfile, join

import netCDF4
import numpy as np
from netCDF4 import Dataset, date2index
from dateutil.parser import parse
from datetime import time, timedelta, datetime
import geopyspark as gps
from shapely.geometry import Polygon


def open_file(path):
    return Dataset(path, 'r', format='NETCDF4')


def get_indexes(lat_array, lon_array, shape, lat, lon):
    flattened_lat = lat_array.flatten()
    flattened_lon = lon_array.flatten()

    index = (np.square(flattened_lat - lat) + np.square(flattened_lon - lon)).argmin()

    return int(index / shape[1]), int(index % shape[1])  # y, x


def get_bounding_box_polygon(lat_array, lon_array, shape, polygon_extent):
    # Transforms a lat/lon-polygon to x/y-coordinates
    # todo: investigate better ways
    y_slice_start, x_slice_start = get_indexes(lat_array, lon_array, shape, polygon_extent.ymin, polygon_extent.xmin)
    y_slice_stop, x_slice_stop = get_indexes(lat_array, lon_array, shape, polygon_extent.ymax, polygon_extent.xmax)

    return x_slice_start, x_slice_stop, y_slice_start, y_slice_stop


def process_query(geojson_shape, date_range, request_vars, spark_ctx):

    nc_base_path = 'data'
    nc_files = [nc_base_path + '/' + f for f in listdir(nc_base_path) if
                isfile(join(nc_base_path, f)) and f.endswith('.nc')]

    request_date_range = date_range.split(',')
    request_start_date = parse(request_date_range[0]).date()
    request_end_date = parse(request_date_range[1]).date()
    print('Request time range: {} - {}'.format(request_start_date, request_end_date))
    print('Request variables: {}'.format(request_vars))

    files_to_analyze = dict()
    for f in nc_files:
        nc_file = open_file(f)
        file_vars = nc_file.variables.keys()

        file_time_range = nc_file['time'][:]
        file_start_date = (datetime(1990, 1, 1, 0, 0) + timedelta(hours=float(file_time_range.min()))).date()
        file_end_date = (datetime(1990, 1, 1, 0, 0) + timedelta(hours=float(file_time_range.max()))).date()

        if file_start_date <= request_end_date and file_end_date >= request_start_date:
            contained_vars = set(request_vars).intersection(file_vars)
            for v in contained_vars:
                if v not in files_to_analyze:
                    files_to_analyze[v] = [f]
                else:
                    files_to_analyze[v] = files_to_analyze[v].append(f)
        nc_file.close()
    print('Matching files: {}'.format(files_to_analyze))

    def process(var_name, nc_file_list):
        print('variable: {}, files {}'.format(var_name, nc_file_list))

        # At some point, we'll want to support partitioned files. For now, just take the first one.
        nc_file = open_file(nc_file_list[0])

        lat_array = nc_file['lat'][:]
        lon_array = nc_file['lon'][:]

        no_data_value = nc_file[var_name].getncattr('_FillValue')

        # Transform the geojson into shapes. We need the shapes represented both as
        # indices in the lat-/lon-arrays (to read only the required slices from netcdf)
        # and as x-/y-values (to mask the constructed layout).
        x_coords = nc_file['x'][:]
        y_coords = nc_file['y'][:]
        mask_shapes_indices = []
        mask_shapes_xy = []
        for feature in geojson_shape['features']:
            # Get each vertex's index in the lat- and lon-arrays
            vertex_indices = np.array(list(get_indexes(lat_array, lon_array, lon_array.shape,
                                                       vertex[1], vertex[0])
                                           for vertex in feature['geometry']['coordinates'][0]))
            mask_shapes_indices.append(vertex_indices)

            # Get the corresponding x and y values
            vertex_xs = x_coords[np.array(vertex_indices)[:, 1]]
            vertex_ys = y_coords[np.array(vertex_indices)[:, 0]]

            # Transform into a polygon
            polygon = Polygon(zip(vertex_xs, vertex_ys))
            mask_shapes_xy.append(polygon)

        # Get the slices to read from netcdf
        y_slice_start = int(min(s[:, 0].min() for s in mask_shapes_indices))
        x_slice_start = int(min(s[:, 1].min() for s in mask_shapes_indices))
        y_slice_stop = int(max(s[:, 0].max() for s in mask_shapes_indices))
        x_slice_stop = int(max(s[:, 1].max() for s in mask_shapes_indices))

        x = x_slice_stop - x_slice_start + 1
        y = y_slice_stop - y_slice_start + 1
        print('x: {}, y: {}, (x_start x_stop y_start y_stop): {}'.format(x, y, (x_slice_start, x_slice_stop,
                                                                                y_slice_start, y_slice_stop)))

        # Get indices of the request's time range
        start_time_index, end_time_index = date2index([datetime.combine(request_start_date, time(0, 0)),
                                                       datetime.combine(request_end_date, time(0, 0))],
                                                      nc_file['time'])

        print('time slice indices: {} - {}'.format(start_time_index, end_time_index))

        # Read the section specified by the request (i.e. specified time and x/y-section)
        variable = nc_file[var_name]
        var_data = variable[start_time_index:end_time_index + 1, y_slice_start:y_slice_stop + 1,
                            x_slice_start:x_slice_stop + 1]
        var_long_name = variable.getncattr('long_name')
        var_unit = variable.getncattr('units')
        var_temp_resolution = variable.getncattr('temporal_resolution')
        x_coords = nc_file['x'][x_slice_start:x_slice_stop + 1]
        y_coords = nc_file['y'][y_slice_start:y_slice_stop + 1]
        lats = nc_file['lat'][y_slice_start:y_slice_stop + 1, x_slice_start:x_slice_stop + 1]
        lons = nc_file['lon'][y_slice_start:y_slice_stop + 1, x_slice_start:x_slice_stop + 1]
        start_instant = datetime.combine(request_start_date, time(0, 0))
        end_instant = datetime.combine(request_end_date, time(0, 0))

        proj_var = nc_file.get_variables_by_attributes(grid_mapping_name=lambda v: v is not None)[0]
        nc_metadata = {attr: nc_file.getncattr(attr) for attr in nc_file.ncattrs()}

        crs = '+proj=laea +lat_0=90 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84 +datum=WGS84 +units=m no_defs'#'+proj=longlat +datum=WGS84 +no_defs ',#'+proj=merc +lon_0=0 +k=1 +x_0=0 +y_0=0 +a=6378137 +b=6378137 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs ',
        x_min = float(min(s.bounds[0] for s in mask_shapes_xy))
        y_min = float(min(s.bounds[1] for s in mask_shapes_xy))
        x_max = float(max(s.bounds[2] for s in mask_shapes_xy))
        y_max = float(max(s.bounds[3] for s in mask_shapes_xy))
        bounding_box = gps.ProjectedExtent(extent=gps.Extent(x_min, y_min, x_max, y_max), proj4=crs)
        #bounds = gps.Bounds(gps.SpaceTimeKey(col=0, row=0, instant=start_instant),
        #                    gps.SpaceTimeKey(col=0, row=0, instant=end_instant))
        #layout = gps.TileLayout(1, 1, x, y)
        #layout_definition = gps.LayoutDefinition(bbox, layout)
        #layer_metadata = gps.Metadata(
        #    bounds=bounds,
        #    crs=crs,
        #    cell_type='float32ud-1.0',
        #    extent=bbox,
        #    layout_definition=layout_definition)

        tile = gps.Tile.from_numpy_array(var_data, no_data_value)
        rdd = spark_ctx.parallelize([(bounding_box, tile)])#gps.SpaceTimeKey(row=0, col=0, instant=start_instant), tile)])

        raster_layer = gps.RasterLayer.from_numpy_rdd(layer_type=gps.LayerType.SPATIAL, numpy_rdd=rdd)#, metadata=layer_metadata)
        tiled_raster_layer = raster_layer.tile_to_layout(gps.LocalLayout(y, x))

        masked_layer = tiled_raster_layer.mask(mask_shapes_xy)
        masked_var_data = masked_layer.to_numpy_rdd().collect()[0][1].cells
        generate_output_netcdf('gddp{}{}.nc'.format(var_name, date_range.replace(',', '-')), x_coords, y_coords, lats,
                               lons, start_instant, end_instant, var_name, var_long_name, var_unit, var_temp_resolution,
                               masked_var_data, no_data_value, nc_metadata, proj_var)

        histogram = masked_layer.get_histogram()
        color_ramp = [0x2791C3FF, 0x5DA1CAFF, 0x83B2D1FF, 0xA8C5D8FF,
                      0xCCDBE0FF, 0xE9D3C1FF, 0xDCAD92FF, 0xD08B6CFF,
                      0xC66E4BFF, 0xBD4E2EFF]
        color_map = gps.ColorMap.from_histogram(histogram, color_ramp)

        # Write image to file
        png = masked_layer.to_png_rdd(color_map).collect()
        with open('gddp{}{}.png'.format(var_name, date_range.replace(',', '-')), 'wb') as f:
            f.write(png[0][1])

        nc_file.close()

    for var_name in files_to_analyze.keys():
        process(var_name, files_to_analyze[var_name])


def generate_output_netcdf(path, x_coords, y_coords, lats, lons, start_datetime, end_datetime, var_name, var_long_name,
                           var_unit, var_temp_resolution, variable, no_data_value, meta, proj_var,
                           lat_name='lat', lon_name='lon', dim_x_name = 'x', dim_y_name = 'y'):

    out_nc = netCDF4.Dataset(path, 'w')

    # define dimensions
    out_nc.createDimension(dim_x_name, variable.shape[2])
    out_nc.createDimension(dim_y_name, variable.shape[1])
    out_nc.createDimension('time', None)

    grid_map_name = proj_var.getncattr('grid_mapping_name')
    proj_units = proj_var.getncattr('units')

    # create variables
    # original coordinate variables
    proj_x = out_nc.createVariable('x', x_coords.dtype, (dim_x_name, ))
    proj_x.units = proj_units
    proj_x.standard_name = 'projection_x_coordinate'
    proj_x.long_name = 'x coordinate of projection'
    proj_x[:] = x_coords

    proj_y = out_nc.createVariable('y', x_coords.dtype, (dim_y_name, ))
    proj_y.units = proj_units
    proj_y.standard_name = 'projection_y_coordinate'
    proj_y.long_name = 'y coordinate of projection'
    proj_y[:] = y_coords

    # auxiliary coordinate variables lat and lon
    lat = out_nc.createVariable(lat_name, 'f4', (dim_y_name, dim_x_name, ))
    lat.units = 'degrees_north'
    lat.standard_name = 'latitude'
    lat.long_name = 'latitude coordinate'
    lat[:] = lats[:]

    lon = out_nc.createVariable(lon_name, 'f4', (dim_y_name, dim_x_name, ))
    lon.units = 'degrees_east'
    lon.standard_name = 'longitude'
    lon.long_name = 'longitude coordinate'
    lon[:] = lons[:]

    # time variable
    var_time = out_nc.createVariable('time', 'i4', ('time', ))
    var_time.units = 'hours since 1990-01-01 00:00:00'
    var_time.calendar = 'gregorian'
    var_time.standard_name = 'time'
    var_time.axis = 'T'

    # data for time variable
    var_time[:] = netCDF4.date2num([start_datetime], units=var_time.units, calendar=var_time.calendar)

    # grid mapping variable
    grid_map = out_nc.createVariable(grid_map_name, 'c', )
    for attr in proj_var.ncattrs():
        grid_map.setncattr(attr, proj_var.getncattr(attr))

    # create data variable
    var_data = out_nc.createVariable(var_name, variable.dtype, ('time', dim_y_name, dim_x_name, ),
                                     fill_value=no_data_value)

    var_data.units = var_unit
    var_data.long_name = var_long_name
    var_data.coordinates = '{} {}'.format(lat_name, lon_name)
    var_data.grid_mapping = grid_map_name

    # assign the masked array to data variable
    data = np.ma.masked_invalid(variable)
    data.set_fill_value(no_data_value)
    var_data[:] = data

    # temporal resolution attribute for the data variable
    var_data.setncattr('temporal_resolution', var_temp_resolution)

    meta.pop('DATETIME', None)
    meta.pop('DOCUMENTNAME', None)
    out_nc.setncatts(meta)

    out_nc.close()
