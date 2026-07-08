const formatter = function (params) {
  if (!params || !params.length) return '';
  let html = params[0].axisValueLabel + '<br/>';
  params.forEach((param) => {
    if (param.seriesType === 'candlestick') {
      const val = param.value;
      let o, c, l, h;
      if (Array.isArray(val) && val.length >= 5) {
        o = val[1]; c = val[2]; l = val[3]; h = val[4];
      } else if (Array.isArray(val) && val.length === 4) {
        o = val[0]; c = val[1]; l = val[2]; h = val[3];
      } else {
        o = '-'; c = '-'; l = '-'; h = '-';
      }
      html += `${param.marker} ${param.seriesName}<br/>`;
      html += `&nbsp;&nbsp;开盘: ${o}<br/>`;
      html += `&nbsp;&nbsp;收盘: ${c}<br/>`;
      html += `&nbsp;&nbsp;最低: ${l}<br/>`;
      html += `&nbsp;&nbsp;最高: ${h}<br/>`;
    }
  });
  return html;
};

// Mock ECharts param structure for candlestick
const mockParams = [{
  axisValueLabel: '2026-07-07',
  seriesType: 'candlestick',
  seriesName: 'K线',
  marker: '<span style="..."></span>',
  // ECharts injects [categoryIndex, open, close, lowest, highest]
  value: [299, 51.59, 55.98, 50.46, 62.25]
}];

console.log("=== Verification Evidence ===");
console.log("Mocked ECharts param.value: ", mockParams[0].value);
console.log("Formatter Output:");
console.log(formatter(mockParams));
console.log("=============================");
