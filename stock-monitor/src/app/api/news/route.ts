import { NextResponse } from 'next/server';

interface SinaNewsItem {
  ctime: string | number;
  docid?: string;
  oid?: string | number;
  title?: string;
  media_name?: string;
  intro?: string;
  url?: string;
}

interface SinaNewsResponse {
  result?: {
    data?: SinaNewsItem[];
  };
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const num = searchParams.get('num') || '20';
  
  try {
    const url = `https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=${num}&page=1`;
    const res = await fetch(url, { cache: 'no-store' });
    const json = await res.json() as SinaNewsResponse;
    
    if (json.result?.data) {
      const newsItems = json.result.data.map((item) => {
        // Parse the timestamp
        const date = new Date(Number(item.ctime) * 1000);
        // Format as YYYY-MM-DD HH:mm:ss
        const formattedDate = date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }).replace(/\//g, '-');
        
        return {
          id: item.docid || String(item.oid ?? ''),
          title: item.title ?? '',
          source: item.media_name || '新浪财经',
          publishTime: formattedDate,
          summary: item.intro || item.title,
          sentiment: '未分析',
          url: item.url ?? ''
        };
      });
      return NextResponse.json({ data: newsItems });
    }
    return NextResponse.json({ data: [] });
  } catch (error) {
    console.error('Error fetching Sina news:', error);
    return NextResponse.json({ data: [], error: 'Failed to fetch news' }, { status: 500 });
  }
}
