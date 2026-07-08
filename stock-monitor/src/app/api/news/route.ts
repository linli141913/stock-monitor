import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const num = searchParams.get('num') || '20';
  
  try {
    const url = `https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=${num}&page=1`;
    const res = await fetch(url, { cache: 'no-store' });
    const json = await res.json();
    
    if (json?.result?.data) {
      const newsItems = json.result.data.map((item: any) => {
        // Parse the timestamp
        const date = new Date(parseInt(item.ctime) * 1000);
        // Format as YYYY-MM-DD HH:mm:ss
        const formattedDate = date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }).replace(/\//g, '-');
        
        return {
          id: item.docid || String(item.oid),
          title: item.title,
          source: item.media_name || '新浪财经',
          publishTime: formattedDate,
          summary: item.intro || item.title,
          sentiment: '中性', // Default fallback
          url: item.url
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
