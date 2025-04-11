import streamlit as st
import geopandas as gpd
import folium
from shapely.geometry import Point
import json
import pandas as pd
import streamlit.components.v1 as components
import os
import atexit
import plotly.express as px


# Cache the GeoJSON data with minimal processing
@st.cache_data
def load_parcels():
    try:
        gdf = gpd.read_parquet('optimized.parquet')
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
        return gdf[['OWNERNME1', 'PLACENAME', 'ZIPCODE', 'SCHOOLDIST', 'ESTFMKVALU', 'PSTLADRESS', 'SITEADRESS', 'GISACRES', 'URL', 'geometry']]
    except FileNotFoundError:
        st.error("Parcel data file not found. Please ensure 'optimized.parquet' exists.")
        return None
    except Exception as e:
        st.error(f"Error loading parcel data: {str(e)}")
        return None

# Cache filter options as a dictionary
@st.cache_data
def get_filter_options(zipcodes, placenames, schooldists):
    return {
        'zipcodes': sorted(zipcodes),
        'placenames': sorted(placenames),
        'schooldists': sorted(schooldists),
        'estfmkvalu_min': 0,
        'estfmkvalu_max': 1000000000
    }

# Optimized filter function
def filter_parcels(gdf, acres_min, acres_max, owner_name, placenames, zipcodes, schooldists, estfmkvalu_min, estfmkvalu_max, lat=None, lon=None, distance_miles=None):
    filtered = gdf.copy()
    
    mask = (filtered['GISACRES'].between(acres_min, acres_max, inclusive='both'))
    if owner_name:
        mask &= filtered['OWNERNME1'].str.contains(owner_name, case=False, na=False)
    if placenames:
        mask &= filtered['PLACENAME'].isin(placenames)
    if zipcodes:
        mask &= filtered['ZIPCODE'].isin(zipcodes)
    if schooldists:
        mask &= filtered['SCHOOLDIST'].isin(schooldists)
    if estfmkvalu_min is not None and estfmkvalu_max is not None:
        mask &= filtered['ESTFMKVALU'].between(estfmkvalu_min, estfmkvalu_max, inclusive='both')
    
    filtered = filtered[mask]
    
    if lat and lon and distance_miles:
        if not (42.0 <= lat <= 44.0 and -89.0 <= lon <= -87.0):
            st.error("Coordinates out of valid range for Waukesha County.")
            return gpd.GeoDataFrame()
        point = Point(lon, lat)
        point_gdf = gpd.GeoDataFrame([{'geometry': point}], crs="EPSG:4326")
        utm_crs = "EPSG:32616"
        point_gdf = point_gdf.to_crs(utm_crs)
        filtered = filtered.to_crs(utm_crs)
        distance_meters = distance_miles * 1609.34
        buffered_point = point_gdf.geometry[0].buffer(distance_meters)
        filtered = filtered[filtered.geometry.intersects(buffered_point)]
        filtered = filtered.to_crs("EPSG:4326")
    
    filtered['popup_content'] = filtered.apply(
        lambda row: (
            f"Owner: {row['OWNERNME1']}<br>"
            f"Acres: {row['GISACRES']}<br>"
            f"Market Value: {row['ESTFMKVALU']}<br>"
            f"Tax Site: <a href='{row['URL']}' target='_blank'>Link to Tax Site</a>"
        ) if pd.notnull(row['URL']) else (
            f"Owner: {row['OWNERNME1']}<br>"
            f"Acres: {row['GISACRES']}<br>"
            f"Market Value: {row['ESTFMKVALU']}<br>"
            "Tax Site: No URL available"
        ),
        axis=1
    )
    return filtered

# Optimized map generation with truncation message
def generate_map_html(filtered_gdf, map_bounds=None):
    m = folium.Map(location=[43.0111125, -88.2275077], zoom_start=10, max_zoom=22)
    
    if map_bounds:
        m.fit_bounds(map_bounds)

    map_truncated = False
    if filtered_gdf is not None and not filtered_gdf.empty:
        if len(filtered_gdf) > 1000:
            geojson_data = json.loads(filtered_gdf.head(1000).to_json())
            map_truncated = True
        else:
            geojson_data = json.loads(filtered_gdf.to_json())
        
        folium.GeoJson(
            geojson_data,
            name="Parcels",
            style_function=lambda x: {'fillColor': 'blue', 'color': 'black', 'weight': 1, 'fillOpacity': 0.3},
            highlight_function=lambda x: {'weight': 3, 'fillOpacity': 0.6},
            tooltip=folium.GeoJsonTooltip(
                fields=['OWNERNME1', 'GISACRES', 'ESTFMKVALU'],
                aliases=['Owner:', 'Acres:', 'Market Value:'],
                localize=True,
                labels=True,
                sticky=True,
                # Append "Click for more details" to the tooltip
                extra_html='<br><i>Click for more details</i>'
            ),
            popup=folium.GeoJsonPopup(
                fields=['popup_content'],
                aliases=[''],
                parse_html=True,
                max_width=300
            )
        ).add_to(m)
        
        if len(filtered_gdf) < 200:
            for _, row in filtered_gdf.iterrows():
                centroid = row['geometry'].centroid
                folium.Marker(
                    location=[centroid.y, centroid.x],
                    popup=row['popup_content'],
                    icon=folium.Icon(color='red', icon='info-sign')
                ).add_to(m)

    folium.LayerControl().add_to(m)
    map_file = "temp_map.html"
    m.save(map_file)
    return map_file, map_truncated

# Filter application logic with custom sliders and reset button
def apply_filters_from_form(gdf, filter_options):
    with st.sidebar.form(key='filter_form'):
        st.header("Filter Parcels")
        acres_options = [0, 0.5, 1, 2, 3, 5, 10, 15, 20, 40, 1000]
        acres_range = st.select_slider(
            "Filter Acres",
            options=acres_options,
            value=(0, 1000),
            key="acres_slider"
        )
        acres_min, acres_max = acres_range
        
        owner_name = st.text_input("Owner Name (partial match)", "", key="owner_input")
        
        zipcodes = st.multiselect("Zip Code", filter_options['zipcodes'], default=[], key="zipcode_select")
        placenames = st.multiselect("Place Name", filter_options['placenames'], default=[], key="placename_select")
        schooldists = st.multiselect("School District", filter_options['schooldists'], default=[], key="schooldist_select")
        
        value_options = [0, 50000, 100000, 200000, 300000, 400000, 500000, 600000, 800000, 900000, 1000000, 1500000, 2000000, 1000000000]
        value_range = st.select_slider(
            "Estimated Market Value Range",
            options=value_options,
            value=(0, 1000000000),
            key="value_slider"
        )
        estfmkvalu_min_val, estfmkvalu_max_val = value_range

        use_distance = st.checkbox("Filter by Distance from Point", key="distance_checkbox")
        lat, lon, distance_miles = None, None, None
        if use_distance:
            lat = st.number_input("Latitude", min_value=42.0, max_value=44.0, value=43.0111125, key="lat_input")
            lon = st.number_input("Longitude", min_value=-89.0, max_value=-87.0, value=-88.2275077, key="lon_input")
            distance_miles = st.slider("Distance (miles)", 0.1, 20.0, 3.0, key="distance_slider")

        col1, col2 = st.columns(2)
        with col1:
            apply_filters = st.form_submit_button("Apply Filters")
        with col2:
            reset_filters = st.form_submit_button("Reset Map")
    
    if apply_filters:
        with st.spinner("Filtering parcels..."):
            return filter_parcels(
                gdf, acres_min, acres_max, owner_name, placenames, zipcodes, schooldists, 
                estfmkvalu_min_val, estfmkvalu_max_val, lat, lon, distance_miles
            )
    elif reset_filters:
        st.session_state.filtered_gdf = None
        st.session_state.map_bounds = None
        st.session_state.map_file = None
        st.session_state.map_truncated = False
        return None
    return None

# Cleanup temporary files
def cleanup():
    if os.path.exists("temp_map.html"):
        os.remove("temp_map.html")
atexit.register(cleanup)

# Main app
def main():
    st.title("Waukesha County Parcel Viewer")

    with st.spinner("Loading parcel data..."):
        gdf = load_parcels()
    if gdf is None:
        st.stop()

    # Pre-compute filter options once
    filter_options = get_filter_options(
        gdf['ZIPCODE'].dropna().unique().tolist(),
        gdf['PLACENAME'].dropna().unique().tolist(),
        gdf['SCHOOLDIST'].dropna().unique().tolist()
    )

    if 'filtered_gdf' not in st.session_state:
        st.session_state.filtered_gdf = None
    if 'map_bounds' not in st.session_state:
        st.session_state.map_bounds = None
    if 'map_file' not in st.session_state:
        st.session_state.map_file = None
    if 'map_truncated' not in st.session_state:
        st.session_state.map_truncated = False

    # Apply filters
    filtered_gdf = apply_filters_from_form(gdf, filter_options)
    if filtered_gdf is not None:
        st.session_state.filtered_gdf = filtered_gdf
        if not filtered_gdf.empty:
            with st.spinner("Generating map..."):
                bounds = filtered_gdf.total_bounds
                buffer_factor = 0.1
                width = bounds[2] - bounds[0]
                height = bounds[3] - bounds[1]
                st.session_state.map_bounds = [
                    [bounds[1] - height * buffer_factor, bounds[0] - width * buffer_factor],
                    [bounds[3] + height * buffer_factor, bounds[2] + width * buffer_factor]
                ]
                st.session_state.map_file, st.session_state.map_truncated = generate_map_html(filtered_gdf, st.session_state.map_bounds)
        else:
            st.warning("No parcels match your filters.")
            st.session_state.map_bounds = None
            st.session_state.map_file = None
            st.session_state.map_truncated = False

    # Generate and display map based on current filtered data
    if st.session_state.filtered_gdf is not None and not st.session_state.filtered_gdf.empty:
        with st.spinner("Updating map..."):
            st.session_state.map_file, st.session_state.map_truncated = generate_map_html(st.session_state.filtered_gdf, st.session_state.map_bounds)
    
    # Display map with truncation message
    if st.session_state.map_file and os.path.exists(st.session_state.map_file):
        with open(st.session_state.map_file, 'r') as f:
            components.html(f.read(), width=700, height=500)
        if st.session_state.map_truncated:
            st.warning("Map truncated: Only the first 1000 parcels are displayed.")
    else:
        default_map = folium.Map(location=[43.0111125, -88.2275077], zoom_start=10, max_zoom=22)
        default_map.save("default_map.html")
        with open("default_map.html", 'r') as f:
            components.html(f.read(), width=700, height=500)

    # Display parcel count and data with truncation message
    if st.session_state.filtered_gdf is not None:
        st.sidebar.write(f"Showing {len(st.session_state.filtered_gdf)} parcels")
        if not st.session_state.filtered_gdf.empty:
            st.write("### Filtered Parcels")
            
            # Display table with truncation
            if len(st.session_state.filtered_gdf) > 1000:
                st.write(st.session_state.filtered_gdf[['OWNERNME1', 'PLACENAME', 'ZIPCODE', 'SCHOOLDIST', 'ESTFMKVALU', 'PSTLADRESS', 'SITEADRESS', 'GISACRES', 'URL']].head(1000))
                st.warning("Table truncated: Only the first 1000 parcels are shown.")
            else:
                st.write(st.session_state.filtered_gdf[['OWNERNME1', 'PLACENAME', 'ZIPCODE', 'SCHOOLDIST', 'ESTFMKVALU', 'PSTLADRESS', 'SITEADRESS', 'GISACRES', 'URL']])

            # Histogram visualization
            st.write("### Parcel Distribution")
            metric = st.selectbox(
                "Select Metric for Distribution",
                options=["GISACRES", "ESTFMKVALU"],
                format_func=lambda x: "Acres" if x == "GISACRES" else "Market Value"
            )
            fig = px.histogram(
                st.session_state.filtered_gdf,
                x=metric,
                nbins=50,
                title=f"Distribution of {metric}",
                labels={metric: "Acres" if metric == "GISACRES" else "Market Value ($)"},
                template="plotly_white"
            )
            fig.update_layout(bargap=0.2)
            st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
    print("1")
