"""
MediaCrawler MySQL database query wrapper for Chinese social media sentiment analysis.

Provides lightweight query capabilities for sentiment analysis of posts from
various Chinese social media platforms (Weibo, Xiaohongshu, Douyin, etc.)
stored in MediaCrawler MySQL databases.
"""

import logging
from datetime import datetime
from typing import Optional

# Optional pymysql import with graceful degradation
try:
    import pymysql
    _PYMYSQL_AVAILABLE = True
except ImportError:
    _PYMYSQL_AVAILABLE = False
    pymysql = None

__all__ = [
    "MediaCrawlerDB",
    "query_mediacrawler_sentiment",
    "_PYMYSQL_AVAILABLE",
]

logger = logging.getLogger(__name__)

# Platform table mapping (platform_name -> table_name)
_PLATFORM_TABLE_MAP = {
    "weibo": "weibo_note",
    "xhs": "xhs_note",
    "douyin": "douyin_aweme",
    "kuaishou": "kuaishou_video",
    "bili": "bilibili_video",
    "zhihu": "zhihu_content",
    "tieba": "tieba_note",
}

# Platform display name mapping (platform_name -> Chinese name)
_PLATFORM_DISPLAY_NAME = {
    "weibo": "微博",
    "xhs": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bili": "B站",
    "zhihu": "知乎",
    "tieba": "贴吧",
}

# Sentiment keywords for bullish signals (positive market sentiment)
_BULLISH_KEYWORDS = [
    "买入", "加仓", "满仓", "涨停", "看多", "抄底", "牛市", "上涨", "突破", "新高", "主升", "反弹", "看好", "做多", "趋势"
]

# Sentiment keywords for bearish signals (negative market sentiment)
_BEARISH_KEYWORDS = [
    "卖出", "清仓", "止损", "跌停", "看空", "割肉", "熊市", "下跌", "破位", "新低", "崩盘", "出货", "跑路", "利空", "减持", "套牢"
]


class MediaCrawlerDB:
    """
    MediaCrawler MySQL database query wrapper.
    
    Provides methods to query social media data from MediaCrawler databases
    with built-in hotness ranking and sentiment analysis support.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "mediacrawler",
        charset: str = "utf8mb4"
    ):
        """
        Initialize MediaCrawlerDB connection.
        
        Args:
            host: MySQL server host
            port: MySQL server port
            user: Database username
            password: Database password
            database: Database name
            charset: Character encoding
        """
        self._connection_params = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": charset,
        }
        self._conn: Optional[pymysql.Connection] = None
        
        if not _PYMYSQL_AVAILABLE:
            logger.warning("pymysql not available, database operations disabled")
            return
        
        try:
            self._conn = pymysql.connect(**self._connection_params)
        except Exception as e:
            logger.warning(f"Failed to connect to database: {e}")
            self._conn = None

    def _ensure_connection(self) -> bool:
        """Ensure database connection is active."""
        if not _PYMYSQL_AVAILABLE:
            return False
        if self._conn is None:
            try:
                self._conn = pymysql.connect(**self._connection_params)
            except Exception as e:
                logger.warning(f"Failed to reconnect to database: {e}")
                return False
        if not self._conn.open:
            try:
                self._conn.ping(reconnect=True)
            except Exception:
                try:
                    self._conn = pymysql.connect(**self._connection_params)
                except Exception as e:
                    logger.warning(f"Failed to reconnect to database: {e}")
                    self._conn = None
                    return False
        return True

    def _query(self, sql: str, params: tuple = ()) -> list:
        """
        Execute SQL query and return results as list of dicts.
        
        Args:
            sql: SQL query string
            params: Query parameters tuple
            
        Returns:
            List of dictionaries representing rows, empty list on error
        """
        if not self._ensure_connection():
            return []
        
        try:
            with self._conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        except Exception as e:
            logger.warning(f"Query execution failed: {e}")
            return []

    def search_by_keyword(
        self,
        keyword: str,
        platform: str = "weibo",
        hours_back: int = 168,
        limit: int = 100
    ) -> list[dict]:
        """
        Query posts by keyword matching title or content.
        
        Args:
            keyword: Search keyword
            platform: Platform name (weibo, xhs, douyin, kuaishou, bili, zhihu, tieba)
            hours_back: Search window in hours from now
            limit: Maximum number of results
            
        Returns:
            List of dicts with keys: title, content, author, liked_count, 
            comment_count, share_count, publish_time, url, hotness
        """
        table_name = _PLATFORM_TABLE_MAP.get(platform, "weibo_note")
        
        # Hotness formula: liked_count * 1 + comment_count * 5 + COALESCE(forward_count,0) * 10
        hotness_sql = """
            liked_count * 1 + comment_count * 5 + COALESCE(forward_count, 0) * 10
        """
        
        sql = f"""
            SELECT 
                title,
                content,
                author,
                liked_count,
                comment_count,
                share_count,
                publish_time,
                url,
                {hotness_sql} as hotness
            FROM {table_name}
            WHERE (title LIKE %s OR content LIKE %s)
              AND publish_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY hotness DESC
            LIMIT %s
        """
        
        like_pattern = f"%{keyword}%"
        params = (like_pattern, like_pattern, hours_back, limit)
        
        results = self._query(sql, params)
        
        # Normalize field names and handle missing fields
        normalized = []
        for row in results:
            normalized.append({
                "title": row.get("title", ""),
                "content": row.get("content", ""),
                "author": row.get("author", "未知"),
                "liked_count": row.get("liked_count", 0),
                "comment_count": row.get("comment_count", 0),
                "share_count": row.get("share_count"),
                "publish_time": row.get("publish_time"),
                "url": row.get("url", ""),
                "hotness": row.get("hotness", 0),
            })
        
        return normalized

    def get_trending_topics(
        self,
        platform: str = "weibo",
        hours_back: int = 24,
        limit: int = 20
    ) -> list[dict]:
        """
        Get trending topics by hotness ranking.
        
        Args:
            platform: Platform name
            hours_back: Time window in hours
            limit: Maximum number of results
            
        Returns:
            List of trending posts with hotness ranking
        """
        table_name = _PLATFORM_TABLE_MAP.get(platform, "weibo_note")
        
        # Hotness formula
        hotness_sql = """
            liked_count * 1 + comment_count * 5 + COALESCE(forward_count, 0) * 10
        """
        
        sql = f"""
            SELECT 
                title,
                content,
                author,
                liked_count,
                comment_count,
                share_count,
                publish_time,
                url,
                {hotness_sql} as hotness
            FROM {table_name}
            WHERE publish_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY hotness DESC
            LIMIT %s
        """
        
        return self._query(sql, (hours_back, limit))

    def close(self):
        """Close database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")
            finally:
                self._conn = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def _classify_sentiment(content: str, title: str = "") -> str:
    """
    Classify content sentiment based on keywords.
    
    Args:
        content: Post content
        title: Post title
        
    Returns:
        "bullish", "bearish", or "neutral"
    """
    if not content and not title:
        return "neutral"
    
    text = f"{title} {content}"
    
    bullish_count = sum(1 for kw in _BULLISH_KEYWORDS if kw in text)
    bearish_count = sum(1 for kw in _BEARISH_KEYWORDS if kw in text)
    
    if bullish_count > bearish_count:
        return "bullish"
    elif bearish_count > bullish_count:
        return "bearish"
    return "neutral"


def _get_sentiment_emoji(sentiment: str) -> str:
    """Get emoji for sentiment classification."""
    emoji_map = {
        "bullish": "📈",
        "bearish": "📉",
        "neutral": "💬"
    }
    return emoji_map.get(sentiment, "💬")


def _truncate_content(content: str, max_length: int = 80) -> str:
    """
    Truncate content for display.
    
    Args:
        content: Content to truncate
        max_length: Maximum length
        
    Returns:
        Truncated content with ellipsis if needed
    """
    if not content:
        return ""
    # Clean up whitespace
    content = " ".join(content.split())
    if len(content) <= max_length:
        return content
    return content[:max_length] + "..."


def _format_datetime(dt) -> str:
    """Format datetime for display."""
    if dt is None:
        return "未知时间"
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)


def _format_number(num) -> str:
    """Format number for display with K/M suffixes."""
    if num is None:
        return "0"
    try:
        n = int(num)
        if n >= 1000000:
            return f"{n / 1000000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)
    except (ValueError, TypeError):
        return "0"


def query_mediacrawler_sentiment(
    keyword: str,
    platform: str = "weibo",
    hours_back: int = 168,
    limit: int = 50,
    db_config: Optional[dict] = None
) -> str:
    """
    Main query function for sentiment analysis of social media posts.
    
    Args:
        keyword: Search keyword
        platform: Platform name (weibo, xhs, douyin, kuaishou, bili, zhihu, tieba)
        hours_back: Search window in hours
        limit: Maximum results to query
        db_config: Optional database connection config dict
        
    Returns:
        Formatted string with sentiment analysis results
    """
    # Graceful degradation: pymysql not available
    if not _PYMYSQL_AVAILABLE:
        return (
            "⚠️ 暂时无法执行查询\n"
            "原因: pymysql 模块未安装\n"
            "解决方案: pip install pymysql\n"
            "───────────────\n"
            "Note: pymysql not available\n"
            "Install with: pip install pymysql"
        )
    
    # Build connection parameters
    conn_params = {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "",
        "database": "mediacrawler",
        "charset": "utf8mb4",
    }
    if db_config:
        conn_params.update(db_config)
    
    # Attempt database connection
    try:
        db = MediaCrawlerDB(**conn_params)
        if not db._ensure_connection():
            return (
                "⚠️ 暂时无法执行查询\n"
                "原因: 数据库连接失败\n"
                "───────────────\n"
                "可能原因:\n"
                "• MySQL服务未启动\n"
                "• 数据库配置错误\n"
                "• 网络连接问题\n"
                "───────────────\n"
                f"配置: {conn_params.get('host')}:{conn_params.get('port')}\n"
                f"数据库: {conn_params.get('database')}"
            )
    except Exception as e:
        return (
            "⚠️ 暂时无法执行查询\n"
            f"原因: {str(e)}\n"
            "───────────────\n"
            "请检查数据库配置和网络连接"
        )
    
    try:
        # Execute keyword search
        results = db.search_by_keyword(
            keyword=keyword,
            platform=platform,
            hours_back=hours_back,
            limit=limit
        )
        
        # Graceful degradation: no results found
        if not results:
            platform_cn = _PLATFORM_DISPLAY_NAME.get(platform, platform)
            return (
                f"📊 {platform_cn} 情感分析结果\n"
                f"关键词: {keyword}\n"
                f"时间范围: 近{hours_back}小时\n"
                f"───────────────\n"
                "🔍 查询结果: 未找到数据\n"
                "───────────────\n"
                "可能原因:\n"
                "• 关键词无相关帖子\n"
                "• 时间范围内无数据\n"
                "• 数据表为空\n"
                "───────────────\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        
        # Classify sentiment for each result
        platform_cn = _PLATFORM_DISPLAY_NAME.get(platform, platform)
        classified_results = []
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        
        for row in results:
            sentiment = _classify_sentiment(row.get("content", ""), row.get("title", ""))
            row["sentiment"] = sentiment
            
            if sentiment == "bullish":
                bullish_count += 1
            elif sentiment == "bearish":
                bearish_count += 1
            else:
                neutral_count += 1
            
            classified_results.append(row)
        
        # Build output string
        output_lines = []
        
        # Header
        output_lines.append("=" * 60)
        output_lines.append(f"📊 {platform_cn} 情感分析结果")
        output_lines.append("=" * 60)
        output_lines.append(f"关键词: {keyword}")
        output_lines.append(f"平台: {platform_cn}")
        output_lines.append(f"时间范围: 近{hours_back}小时")
        output_lines.append(f"───────────────")
        
        # Summary statistics
        output_lines.append(f"📈 搜索结果: 共{len(results)}条")
        output_lines.append(f"   • 看多(📈): {bullish_count}条")
        output_lines.append(f"   • 看空(📉): {bearish_count}条")
        output_lines.append(f"   • 中性(💬): {neutral_count}条")
        
        if bullish_count + bearish_count > 0:
            sentiment_ratio = bullish_count / (bullish_count + bearish_count) * 100
            output_lines.append(f"   • 多空比: {sentiment_ratio:.1f}%")
        
        output_lines.append("───────────────")
        
        # Top posts (limit to 30 for readability)
        display_limit = min(30, len(classified_results))
        output_lines.append(f"🔥 热门帖子 TOP {display_limit}")
        output_lines.append("-" * 60)
        
        for i, row in enumerate(classified_results[:display_limit], 1):
            sentiment = row.get("sentiment", "neutral")
            emoji = _get_sentiment_emoji(sentiment)
            author = row.get("author", "未知")
            publish_time = _format_datetime(row.get("publish_time"))
            content = _truncate_content(row.get("content", ""), 80)
            title = row.get("title", "")
            liked = _format_number(row.get("liked_count", 0))
            commented = _format_number(row.get("comment_count", 0))
            shared = _format_number(row.get("share_count", 0))
            hotness = row.get("hotness", 0)
            
            # Post header with ranking and sentiment
            output_lines.append(f"{i:2d}. {emoji} [热度:{int(hotness):,}]")
            
            # Author and time
            output_lines.append(f"    👤 {author} | 🕐 {publish_time}")
            
            # Title if available
            if title:
                title_display = _truncate_content(title, 60)
                output_lines.append(f"    📌 {title_display}")
            
            # Content preview
            if content:
                output_lines.append(f"    💬 {content}")
            
            # Engagement stats
            output_lines.append(f"    👍{liked} 💬{commented} 🔄{shared}")
            output_lines.append("")
        
        # Footer
        output_lines.append("=" * 60)
        output_lines.append(f"📋 统计说明:")
        output_lines.append(f"   热度得分 = 点赞×1 + 评论×5 + 转发×10")
        output_lines.append(f"   看多关键词: {', '.join(_BULLISH_KEYWORDS[:5])}...")
        output_lines.append(f"   看空关键词: {', '.join(_BEARISH_KEYWORDS[:5])}...")
        output_lines.append(f"───────────────")
        output_lines.append(f"查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output_lines.append(f"数据来源: MediaCrawler {platform_cn}数据库")
        
        return "\n".join(output_lines)
        
    except Exception as e:
        logger.warning(f"Query failed: {e}")
        return (
            "⚠️ 查询执行出错\n"
            f"原因: {str(e)}\n"
            "───────────────\n"
            "请稍后重试或检查数据"
        )
    finally:
        db.close()


if __name__ == "__main__":
    # Demo/test code when run directly
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("MediaCrawler Sentiment Analysis Demo")
    print("=" * 60)
    
    if not _PYMYSQL_AVAILABLE:
        print("⚠️ pymysql not installed")
        print("Demo will show structure without live data")
        print()
    
    # Show configuration
    print("Platforms supported:")
    for platform, table in _PLATFORM_TABLE_MAP.items():
        cn_name = _PLATFORM_DISPLAY_NAME.get(platform, platform)
        print(f"  • {platform} -> {table} ({cn_name})")
    
    print()
    print("Sentiment keywords:")
    print(f"  Bullish: {', '.join(_BULLISH_KEYWORDS)}")
    print(f"  Bearish: {', '.join(_BEARISH_KEYWORDS)}")
    
    print()
    print("Example usage:")
    print('  result = query_mediacrawler_sentiment("股票", platform="weibo")')
    print('  print(result)')
    
    print()
    print("=" * 60)
    print("Demo complete")
    print("=" * 60)
