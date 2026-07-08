const echarts = require('echarts');
// Mocking the DOM environment for echarts
const jsdom = require('jsdom');
const { JSDOM } = jsdom;
const { window } = new JSDOM(`<!DOCTYPE html><div id="main" style="width: 600px;height:400px;"></div>`);
global.window = window;
global.document = window.document;
global.navigator = window.navigator;

const chart = echarts.init(document.getElementById('main'));

chart.setOption({
    tooltip: {
        trigger: 'axis',
        formatter: function(params) {
            console.log("TOOLTIP PARAMS:", JSON.stringify(params[0].value));
            console.log("TOOLTIP DATA:", JSON.stringify(params[0].data));
            return "";
        }
    },
    xAxis: {
        type: 'category',
        data: ['2023-01-01', '2023-01-02']
    },
    yAxis: {},
    series: [{
        type: 'candlestick',
        data: [
            [51.59, 55.98, 50.46, 62.25], // Open, Close, Lowest, Highest
            [55.0, 56.0, 54.0, 58.0]
        ]
    }]
});

// simulate tooltip trigger
chart.dispatchAction({
    type: 'showTip',
    x: 100,
    y: 100,
    dataIndex: 0,
    seriesIndex: 0
});
