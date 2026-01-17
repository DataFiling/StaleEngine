import asyncio
from apify import Actor
from playwright.async_api import async_playwright

async def main() -> None:
    async with Actor:
        # 1. Capture the input
        actor_input = await Actor.get_input() or {}
        search_query = actor_input.get('query', 'Tesla')

        async with async_playwright() as playwright:
            # 2. Setup Residential Proxies
            proxy_config = await Actor.create_proxy_configuration(groups=['RESIDENTIAL'])
            proxy_url = await proxy_config.new_url()

            # 3. Launch Browser (Standard Chromium)
            browser = await playwright.chromium.launch(
                headless=True,
                proxy={'server': proxy_url} if proxy_url else None
            )

            # 4. Context with extra "Human" settings
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # Increase default timeout to 60 seconds (Delaware is slow!)
            context.set_default_timeout(60000)
            page = await context.new_page()

            try:
                Actor.log.info(f"Navigating to Delaware search page...")
                
                # 5. Go to the page and wait specifically for the search box
                await page.goto("https://icis.corp.delaware.gov/ecorp/entitysearch/namesearch.aspx", wait_until="commit")
                
                # Explicitly wait for the box to be visible before typing
                search_box_selector = 'input[id*="frmEntityName"]'
                await page.wait_for_selector(search_box_selector, state="visible")
                
                Actor.log.info(f"Search box found. Typing '{search_query}'...")
                await page.locator(search_box_selector).fill(search_query)
                await page.click('input[id*="btnSubmit"]')

                # 6. Wait for results
                table_selector = 'table[id*="gvResults"]'
                Actor.log.info("Waiting for results table...")
                await page.wait_for_selector(table_selector)

                rows = await page.locator(f"{table_selector} tr").all()
                results = []
                for row in rows[1:]:
                    cells = await row.locator('td').all_inner_texts()
                    if len(cells) >= 2:
                        results.append({
                            "file_number": cells[0].strip(),
                            "entity_name": cells[1].strip()
                        })

                # 7. Save and Log
                if results:
                    await Actor.push_data(results)
                    Actor.log.info(f"Success! Found {len(results)} results.")
                else:
                    Actor.log.warning("Table found but no results extracted.")

            except Exception as e:
                # 8. The "Black Box" Recorder
                Actor.log.error(f"Crash Details: {str(e)}")
                # Take a screenshot to see what happened
                await Actor.set_value('ERROR_SCREENSHOT', await page.screenshot(full_page=True), content_type='image/png')
                Actor.log.info("Screenshot saved to Key-Value Store as 'ERROR_SCREENSHOT'")
                
            finally:
                await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
