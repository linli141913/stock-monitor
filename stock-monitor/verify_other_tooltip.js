const formatter = function (params) {
  if (!params || !params.length) return '';
  let html = params[0].axisValueLabel + '<br/>';
  params.forEach((param) => {
    if (param.seriesType === 'candlestick') {
      // Ignored for this test
    } else {
      let val = param.value;
      if (Array.isArray(val)) {
        val = val[1] !== undefined ? val[1] : val[0];
      } else if (typeof val === 'object' && val !== null) {
        val = val.value || 0;
      }
      if (val !== '-' && val !== undefined && val !== null) {
        html += `${param.marker} ${param.seriesName}: ${val}<br/>`;
      }
    }
  });
  return html;
};

// Mock ECharts param structure for MA and Volume
const mockParams = [
  {
    axisValueLabel: '2026-07-08',
    seriesType: 'line',
    seriesName: 'MA5',
    marker: '<span style="color:#e6b600;"></span>',
    // MA values are usually passed as [categoryIndex, value] or scalar value
    value: [300, 56.12]
  },
  {
    axisValueLabel: '2026-07-08',
    seriesType: 'bar',
    seriesName: '成交量',
    marker: '<span style="color:#ccc;"></span>',
    // Volume values are passed as an object containing value and itemStyle
    value: { value: 1250000, itemStyle: { color: '#eb5454' } }
  }
];

console.log("=== K-line Other Data Verification ===");
console.log("Mocked MA param.value: ", mockParams[0].value);
console.log("Mocked Volume param.value: ", mockParams[1].value);
console.log("Formatter Output:");
console.log(formatter(mockParams));
console.log("======================================");
