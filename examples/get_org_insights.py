import asyncio

from pydantic import SecretStr
from pydantic_settings import BaseSettings
from evnex.api import Evnex


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


async def main():
    creds = EvnexAuthDetails()
    evnex = Evnex(
        username=creds.EVNEX_CLIENT_USERNAME,
        password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value(),
    )

    user_data = await evnex.get_user_detail()

    for org in user_data.organisations:
        # print("Global connector statuses")
        # status = await evnex.get_org_summary_status(org_id=org.slug)
        # print(status)

        print("Getting 7 day insight for", org.name, "User:", user_data.name)
        daily_insights = await evnex.get_org_insight(days=7, org_id=org.slug)

        for segment in daily_insights:
            print(segment)


if __name__ == "__main__":
    asyncio.run(main())
