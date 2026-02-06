from fastapi import FastAPI, HTTPException, Query
from playwright.async_api import async_playwright
import asyncio

app = FastAPI(title="Vidssave Session Generator API (GET)")


@app.get("/")
def root():
    return {"status": "ok"}


def cookies_to_netscape(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        lines.append(
            "\t".join([
                c["domain"],
                "TRUE" if c["domain"].startswith(".") else "FALSE",
                c["path"],
                "TRUE" if c["secure"] else "FALSE",
                str(c["expires"] or 0),
                c["name"],
                c["value"],
            ])
        )
    return "\n".join(lines)


@app.get("/vidssave")
async def generate_session(
    youtube_url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("360P", description="360P / 720P"),
):
    parse_payload = None
    download_url = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        page = await context.new_page()

        def capture_request(req):
            nonlocal parse_payload
            if "/media/parse" in req.url and req.method == "POST":
                body = req.post_data or ""
                if "origin=cache" in body:
                    parse_payload = body

        page.on("request", capture_request)

        # Open site
        await page.goto(
            "https://vidssave.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Cloudflare / slow dyno safety
        await page.wait_for_timeout(5000)

        try:
            await page.wait_for_selector(
                "input[type='text']",
                timeout=60000,
            )
        except:
            await browser.close()
            raise HTTPException(500, "Vidssave input not loaded (blocked or slow)")

        # Submit URL
        await page.fill("input[type='text']", youtube_url)
        await page.keyboard.press("Enter")

        await asyncio.sleep(8)

        if not parse_payload:
            await browser.close()
            raise HTTPException(500, "Failed to capture parse payload")

        # Parse API (same browser session)
        response = await page.evaluate(
            """
            async (payload) => {
                const r = await fetch(
                    "https://api.vidssave.com/api/contentsite_api/media/parse",
                    {
                        method: "POST",
                        headers: {
                            "content-type": "application/x-www-form-urlencoded"
                        },
                        body: payload
                    }
                );
                return await r.json();
            }
            """,
            parse_payload,
        )

        resources = response.get("data", {}).get("resources", [])

        for r in resources:
            if (
                r.get("type") == "video"
                and r.get("format") == "MP4"
                and r.get("quality") == quality
                and r.get("download_mode") == "direct"
            ):
                download_url = r["download_url"]
                break

        if not download_url:
            await browser.close()
            raise HTTPException(404, "Direct download URL not found")

        cookies_json = await context.cookies()
        cookies_netscape = cookies_to_netscape(cookies_json)

        await browser.close()

    return {
        "download_url": download_url,
        "cookies": cookies_json,
        "cookies_netscape": cookies_netscape,
    }
