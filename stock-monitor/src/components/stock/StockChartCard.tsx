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

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

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

  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' }
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
        axisPointer: { z: 100 },
        axisLabel: { showMaxLabel: true } // 强制显示最后一个刻度
      },
      {
        type: 'category',
        gridIndex: 1,
        data: categoryData,
        boundaryGap: false,
        axisLine: { onZero: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
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
            style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', color: '#666' }}
            title={isFullscreen ? "退出全屏" : "全屏查看"}
          >
            {isFullscreen ? <Minimize size={18} /> : <Maximize size={18} />}
          </button>
        </div>
      </div>
      
      <div className={styles.chartContainer} style={{ height: (isFullscreen || isSimulatedFullscreen) ? 'calc(100% - 60px)' : '400px', width: '100%' }}>
        <ReactECharts 
          option={option} 
          style={{ height: '100%', width: '100%' }}
          notMerge={false}
          lazyUpdate={true}
          showLoading={loading}
          onEvents={onEvents}
        />
      </div>
    </div>
  );
}
