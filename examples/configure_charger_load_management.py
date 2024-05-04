import asyncio
import logging

from pydantic_settings import BaseSettings
from pydantic import SecretStr

from evnex.api import Evnex

logging.basicConfig(level=logging.WARNING)


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
        print("Getting charge points for", org.name)
        charge_points = await evnex.get_org_charge_points(org_id=org.id)

        for charge_point in charge_points:
            detail = await evnex.get_charge_point_detail_v3(
                charge_point_id=charge_point.id
            )
            print(detail)

            print("Setting charger schedule")
            schedule = await evnex.set_charge_point_schedule(
                charge_point_id=charge_point.id,
                charging_profile_periods=[
                    {"start": 0, "limit": 32},
                    {"start": 3600, "limit": 0},
                    {"start": 4500, "limit": 0},
                ],
            )
            print(schedule)

            print("Setting charger load management")
            schedule = await evnex.set_charger_load_profile(
                charge_point_id=charge_point.id,
                charging_profile_periods=[
                    {"start": 0, "limit": 32},
                    {"start": 3600, "limit": 0},
                    {"start": 4500, "limit": 0},
                ],
                enabled=False,
            )
            print(schedule)


if __name__ == "__main__":
    asyncio.run(main())
