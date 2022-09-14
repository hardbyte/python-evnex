import asyncio

from pydantic import BaseSettings, SecretStr


from evnex.api import Evnex


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


async def main():
    creds = EvnexAuthDetails()
    evnex = Evnex(username=creds.EVNEX_CLIENT_USERNAME,
                  password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value())

    user_data = await evnex.get_user_detail()

    print("User:", user_data.name, user_data.email, user_data.id)

    for org in user_data.organisations:
        print("Getting charge points for", org.name)
        charge_points = await evnex.get_org_charge_points(org_id=org.id)

        for charge_point in charge_points:

            print(charge_point.name, charge_point.networkStatus, charge_point.serial, charge_point.id)

            print(await evnex.get_charge_point_detail(charge_point_id=charge_point.id))

            print("Getting charge override setting")
            override = await evnex.get_charge_point_override(charge_point_id=charge_point.id)
            print(override)

            print("Solar Config")
            solar_config = await evnex.get_charge_point_solar_config(charge_point_id=charge_point.id)
            print(solar_config)

            print("Setting charge override setting to ON")
            override = await evnex.set_charge_point_override(charge_point_id=charge_point.id, charge_now=True)
            print(override)


            #print("Getting transactions")
            #transactions = await evnex.get_charge_point_transactions(charge_point_id=charge_point.id)
            #print(transactions)

        # print("Stopping charge point")
        # print(await evnex.stop_charge_point(charge_point_id=charge_points[0].id))


if __name__ == '__main__':
    asyncio.run(main())
