import asyncio
from pydantic import BaseSettings, SecretStr
from evnex.api import Evnex

import logging

logging.basicConfig(level=logging.INFO)


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

    print("User:", user_data.name, user_data.email, user_data.id)

    for org in user_data.organisations:
        print("Getting charge points for", org.name)
        charge_points = await evnex.get_org_charge_points(org_id=org.id)

        for charge_point in charge_points:
            detail = await evnex.get_charge_point_detail_v3(
                charge_point_id=charge_point.id
            )
            print(detail)
            await evnex.disable_charger(charge_point_id=charge_point.id)
            await asyncio.sleep(5)
            detail = await evnex.get_charge_point_detail_v3(
                charge_point_id=charge_point.id
            )
            print(detail)

            print("Renabling")
            await evnex.enable_charger(charge_point_id=charge_point.id)

            # Safe to repeat
            await evnex.enable_charger(charge_point_id=charge_point.id)


if __name__ == "__main__":
    asyncio.run(main())
