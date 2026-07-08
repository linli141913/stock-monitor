const echarts = require('echarts');
// Mock DOM
const jsdom = require('jsdom');
const { JSDOM } = jsdom;
const dom = new JSDOM('<!DOCTYPE html><div id="main" style="width: 600px;height:400px;"></div>');
global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;

const chart = echarts.init(document.getElementById('main'));
chart.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { data: ['2023-01-01'] },
    yAxis: {},
    series: [{
        name: 'K线',
        type: 'candlestick',
        dimensions: ['开盘', '收盘', '最低', '最高'],
        data: [[10, 20, 5, 25]]
    }]
});

// simulate hover
chart.dispatchAction({
    type: 'showTip',
    seriesIndex: 0,
    dataIndex: 0
});

setTimeout(() => {
    const tip = document.querySelector('div[style*="z-index: 99999"]');
    console.log(tip ? tip.innerHTML : document.body.innerHTML);
}, 500);
