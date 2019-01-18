import React, {Component} from 'react';
import 'bootstrap/dist/css/bootstrap.css';
import Map from "./components/map";
import {render} from "react-dom";
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import 'bootstrap-daterangepicker/daterangepicker.css'
import 'react-notifications/lib/notifications.css';
import _ from 'lodash';
import axios from "axios";
import {NotificationContainer, NotificationManager} from 'react-notifications';
import saveAs from 'file-saver';
import FileComp from './components/fileComp';
import DataSetComp from './components/dataSetsComp';
import UserInputComp from "./components/userInputComp";

class App extends Component {
    state = {
        selectDateSet: null,
        products: []
    };

    constructor(props) {
        super(props);
        this.child = React.createRef();
        this.userInputs= {};
        this.features=[];
    }

    componentDidMount() {
        let self = this;
        let products = [];
        axios.get(`http://127.0.0.1:5000/getBoundaries`)
            .then(res => {
                if(res.data.length > 0){
                    res.data.forEach(function (p) {
                        let pJson = p[Object.keys(p)[0]];
                        let product = {
                            value: pJson.product,
                            label: pJson.product,
                            vars: _.map(pJson.variables, function(i){
                                return {key: i.short_name, description: i.long_name, select: false}
                            }),
                            color: '#17a2b8',
                            bbox: pJson.domain[0].geometry.coordinates,
                            valid_start_time: pJson.time[0],
                            valid_end_time: pJson.time[pJson.time.length-1]
                        };
                        products.push(product);
                    });
                }
                self.setState({products: products});
            });
    }

    updateDateSet = (dataSet) => {
        this.setState(dataSet);
    };

    updateUserInputs = (userInputs) => {
        this.userInputs = _.assign(this.userInputs, userInputs);
    };

    updateFeatures = (features) => {
        this.features = features;
    };

    postJsonToServer = () => {
        let variables = new Set();
        if(!this.state.selectDateSet){
            NotificationManager.error('No product selected.');
            return;
        }
        this.state.selectDateSet.vars.forEach(v => {
            if(v.selected){
                variables.add(v.key);
            }
        });

        if(variables.size === 0){
            NotificationManager.error('No variable selected.');
            return;
        }
        if(!this.userInputs || !this.userInputs.start_time || !this.userInputs.end_time){
            NotificationManager.error('No date range selected.');
            return;
        }
        let passLoad = {
            variables: Array.from(variables),
            product: this.state.selectDateSet.value,
            bounding_geom: this.features
        };
        passLoad = _.assign(passLoad, this.userInputs);
        console.log(JSON.stringify(passLoad));
        if (window.confirm("Do you want to process?")) {
            axios.post('http://127.0.0.1:5000/fetchResult', passLoad, {responseType: 'blob'})
                .then(function (response) {
                    saveAs(new Blob([response.data], {type:'application/x-netcdf'}));
                })
                .catch(function (error) {
                    console.log(error);
                });

        }
        else{
            // cancel
        }
    };

    renderGeoJSON = (geojson) => {
        this.child.current.renderGeoJson(geojson);
    };



    render() {
        return (
            <div className="row">
                <div className="col col-lg-12">
                    <UserInputComp updateUserInputs={this.updateUserInputs} products={this.state.products} updateDateSet={this.updateDateSet}/>
                </div>
                <div className="col col-lg-3">
                    <DataSetComp selectDateSet={this.state.selectDateSet} updateDateSet={this.updateDateSet} />
                    <div className="card mt-2">
                        <div className="card-body">
                            <FileComp uploadFileCallback={this.updateFeatures} renderGeoJSON={this.renderGeoJSON} />
                        </div>
                    </div>
                    <div className="mt-2">
                        <button className="btn btn-info" onClick={this.postJsonToServer}>Process</button>
                    </div>

                </div>
                <div className="col col-lg-9">
                    <Map ref={this.child} selectDateSet={this.state.selectDateSet} drawCallback={this.updateFeatures} />
                </div>
                <NotificationContainer/>
            </div>

        );
    }
}

render(
    <App />,
    document.getElementById('content')
);

export default App;