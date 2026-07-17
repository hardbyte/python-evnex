import asyncio
import os

from evnex.api import Evnex
from evnex.auth import EvnexAuth


async def main():
    auth = EvnexAuth()
    await auth.start_authentication(
        os.environ["EVNEX_CLIENT_USERNAME"], os.environ["EVNEX_CLIENT_PASSWORD"]
    )
    evnex = Evnex(auth=auth)

    user_data = await evnex.get_user_detail()

    for org in user_data.organisations:
        # print("Global connector statuses")
        # status = await evnex.get_org_summary_status(org_id=org.slug)
        # print(status)

        print("Getting 7 day insight for", org.name, "User:", user_data.name)
        daily_insights = await evnex.get_org_insight(days=7, org_id=org.id)

        for segment in daily_insights:
            print(segment)


if __name__ == "__main__":
    asyncio.run(main())
