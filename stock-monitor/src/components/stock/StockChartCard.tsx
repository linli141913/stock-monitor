'use client';

import React, { useRef, useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { Maximize, Minimize } from 'lucide-react';
import { KlineItem } from '@/types/stock';
import styles from './StockChartCard.module.css';

type PeriodType = 'day' | 'week' | 'month' | 'year';

interface Props {
  data: KlineItem[];
  period: PeriodType;
  loading?: boolean;
  onPeriodChange: (p: PeriodType) => void;
}

const periodMap: Record<PeriodType, string> = {
  'day': '日K',
  'week': '周K',
  'month': '月K',
  'year': '年K'
};
const periodKeys: PeriodType[] = ['day', 'week', 'month', 'year'];

export default function StockChartCard({ data, period, loading, onPeriodChange }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const echartsRef = useRef<any>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isSimulatedFullscreen, setIsSimulatedFullscreen] = useState(false);
  const zoomRef = useRef({ start: 50, end: 100 });
  const prevPeriodRef = useRef(period);

  if (prevPeriodRef.current !== period) {
    zoomRef.current = { start: 50, end: 100 };
    prevPeriodRef.current = period;
  }

  const onEvents = {
    datazoom: (params: any) => {
      const z = params.batch ? params.batch[0] : params;
      if (z.start !== undefined) zoomRef.current.start = z.start;
      if (z.end !== undefined) zoomRef.current.end = z.end;
    }
  };

      

  
  // Custom mobile touch logic: Instant inspect on touch, hide on move or release
  const isInspectingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const isMobile = typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent);
    if (!isMobile) return;
    const chart = echartsRef.current?.getEchartsInstance();
    if (!chart) return;

    const zr = chart.getZr();

    const onTouchStart = (e: any) => {
      startPosRef.current = { x: e.offsetX, y: e.offsetY };
      isInspectingRef.current = true;
      chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
    };

    const onTouchMove = (e: any) => {
      const dx = e.offsetX - startPosRef.current.x;
      const dy = e.offsetY - startPosRef.current.y;
      
      if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
        if (isInspectingRef.current) {
          isInspectingRef.current = false;
          chart.dispatchAction({ type: 'hideTip' });
        }
      } else {
        if (isInspectingRef.current) {
          if (e.event) e.event.preventDefault(); 
          chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
        }
      }
    };

    const onTouchEnd = () => {
      if (isInspectingRef.current) {
        chart.dispatchAction({ type: 'hideTip' });
        isInspectingRef.current = false;
      }
    };

    zr.on('mousedown', onTouchStart);
    zr.on('mousemove', onTouchMove);
    zr.on('mouseup', onTouchEnd);
    zr.on('globalout', onTouchEnd);

    return () => {
      zr.off('mousedown', onTouchStart);
      zr.off('mousemove', onTouchMove);
      zr.off('mouseup', onTouchEnd);
      zr.off('globalout', onTouchEnd);
    };
  }, [data]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  useEffect(() => {
    if (isSimulatedFullscreen) {
      document.body.style.overflow = 'hidden';
      document.documentElement.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
    };
  }, [isSimulatedFullscreen]);

    const toggleFullscreen = () => {
    if (isSimulatedFullscreen) {
      setIsSimulatedFullscreen(false);
      return;
    }
    
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      if (containerRef.current?.requestFullscreen) {
        containerRef.current.requestFullscreen().catch(err => {
          console.error(`Error attempting to enable fullscreen: ${err.message}`);
          setIsSimulatedFullscreen(true);
        });
      } else {
        // Fallback for iOS
        setIsSimulatedFullscreen(true);
      }
    }
  };

  // 处理 ECharts 数据格式
  const categoryData = data.map(item => item.date);
  const values = data.map(item => [item.open, item.close, item.low, item.high]);
  const volumes = data.map(item => ({
    value: item.volume,
    itemStyle: {
      color: item.close >= item.open ? '#ef4444' : '#22c55e'
    }
  }));

  // MA 均线（后端已计算好，null 表示数据不足）
  const ma5  = data.map(item => item.ma5  ?? '-');
  const ma10 = data.map(item => item.ma10 ?? '-');
  const ma20 = data.map(item => item.ma20 ?? '-');

  // 中国股市习惯：红涨绿跌
  const upColor = '#ef4444';
  const upBorderColor = '#ef4444';
  const downColor = '#22c55e';
  const downBorderColor = '#22c55e';

  const isMobileDevice = typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent);

  const option = {
    animation: false,
    tooltip: {
      show: true,
      trigger: 'axis',
      triggerOn: isMobileDevice ? 'none' : 'mousemove|click',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' },
      formatter: function (params: any) {
        if (!params || !params.length) return '';
        let html = params[0].axisValueLabel + '<br/>';
        params.forEach((param: any) => {
          if (param.seriesType === 'candlestick') {
            const val = param.value;
            let o, c, l, h;
            // ECharts 默认在类目轴下，param.value 结构为: [类目索引, 开盘, 收盘, 最低, 最高]
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
      },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (isMobileDevice) {
          let viewW = size.viewSize[0] || window.innerWidth;
          let viewH = size.viewSize[1] || window.innerHeight;
          let boxW = size.contentSize[0] || 150;
          let boxH = size.contentSize[1] || 150;
          
          // 默认放在十字线的左边，防止右手大拇指遮挡
          let x = pos[0] - boxW - 15; 
          let y = (viewH - boxH) / 2;
          
          // 如果十字线太靠左边（手指在最左侧），左边放不下了，才切换到右边显示
          if (x < 5) {
            x = pos[0] + 15;
          }
          
          if (y < 5) y = 5;
          
          return [x, y];
        }
        return undefined;
      }
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: { backgroundColor: '#777' }
    },
    grid: [
      {
        left: '10%',
        right: '5%',
        height: '60%'
      },
      {
        left: '10%',
        right: '5%',
        top: '75%',
        height: '15%'
      }
    ],
    xAxis: [
      {
        type: 'category',
        data: categoryData,
        boundaryGap: false,
        axisLine: { onZero: false },
        splitLine: { show: false },
        min: 'dataMin',
        max: 'dataMax',
        axisPointer: { z: 100, label: { show: !isMobileDevice } },
        axisLabel: { showMaxLabel: true, show: !isMobileDevice } // 电脑端显示在上层图表底部
      },
      {
        type: 'category',
        gridIndex: 1,
        data: categoryData,
        boundaryGap: false,
        axisLine: { onZero: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: { label: { show: !isMobileDevice } },
        axisLabel: { show: isMobileDevice }, // 手机端显示在底层成交量底部
        min: 'dataMin',
        max: 'dataMax'
      }
    ],
    yAxis: [
      {
        scale: true,
        splitArea: { show: false },
        splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false }
      }
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: zoomRef.current.start,
        end: zoomRef.current.end
      },
      {
        show: false,
        xAxisIndex: [0, 1],
        type: 'slider',
        top: '95%',
        start: zoomRef.current.start,
        end: zoomRef.current.end
      }
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        dimensions: ['开盘', '收盘', '最低', '最高'],
        data: values,
        itemStyle: {
          color: upColor,
          color0: downColor,
          borderColor: upBorderColor,
          borderColor0: downBorderColor
        }
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        smooth: true,
        lineStyle: { opacity: 0.8, color: '#f59e0b', width: 1 },
        symbol: 'none'
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        smooth: true,
        lineStyle: { opacity: 0.8, color: '#3b82f6', width: 1 },
        symbol: 'none'
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        smooth: true,
        lineStyle: { opacity: 0.8, color: '#8b5cf6', width: 1 },
        symbol: 'none'
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes
      }
    ]
  };

  return (
    <div className={`${styles.card} ${isSimulatedFullscreen ? styles.simulatedFullscreen : ''}`} ref={containerRef} style={isFullscreen ? { backgroundColor: '#fff', padding: '20px', overflow: 'hidden' } : {}}>
      <div className={styles.header}>
        <div className={styles.tabs}>
          <button
            className={styles.tab}
            disabled
            title="分时图即将上线"
            style={{ opacity: 0.4, cursor: 'not-allowed' }}
          >分时</button>
          {periodKeys.map(p => (
            <button
              key={p}
              className={`${styles.tab} ${period === p ? styles.active : ''}`}
              onClick={() => onPeriodChange(p)}
            >
              {periodMap[p]}
            </button>
          ))}
        </div>
        <div className={styles.legend} style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <span style={{ color: '#f59e0b' }}>MA5</span>
          <span style={{ color: '#3b82f6' }}>MA10</span>
          <span style={{ color: '#8b5cf6' }}>MA20</span>
          <button 
            onClick={toggleFullscreen} 
            style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', color: '#666', fontSize: '13px', fontWeight: 500 }}
            title={isFullscreen ? "退出全屏" : "横屏放大"}
          >
            {isFullscreen || isSimulatedFullscreen ? '[ 退出全屏 ]' : '[ 横屏放大 ]'}
          </button>
        </div>
      </div>
      
      <div className={styles.chartContainer} style={{ height: (isFullscreen || isSimulatedFullscreen) ? 'calc(100% - 60px)' : '400px', width: '100%' }}>
        <ReactECharts 
          ref={echartsRef}
          option={option} 
          style={{ height: '100%', width: '100%' }}
          lazyUpdate={true}
          showLoading={loading}
          onEvents={onEvents}
        />
      </div>
    </div>
  );
}
