from sqlalchemy.orm import Session
from app import models, database
import random


def seed_data():
    models.Base.metadata.create_all(bind=database.engine)

    db = database.SessionLocal()

    if db.query(models.SkuPart).count() > 0:
        print("Data already exists. Skipping seed.")
        db.close()
        return

    default_conversion_rate = 4.0

    config = models.Config(
        default_conversion_rate=default_conversion_rate,
    )
    db.add(config)

    parts_data = [
        {"sku": "ST973402SSUN72G / 540-6611", "name": "SUN 73GB 10K SAS HDD ( 390-0323)", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "QLE2462", "name": "Dual-Port PCIe-to-4Gbps Fibre Channel Adapter", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7066824", "name": 'Sun Oracle 4TB 7.2K SAS 6G 3.5" Hard Drive', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "8200038", "name": "SUN ORACLE 600GB 10K SAS-3 DISK DRIVE", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7018701", "name": "Sun Memory 16GB DDR3-1600 DIMM 1.35V", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7051223", "name": "Sun Dual Port 10GB PCI-E Ethernet SFP+ Adapter", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7046442", "name": "Sun Dual 40Gb/Sec 4x QDR InfiniBand Host Channel Adapter Module", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7047503", "name": "Sun 8-Port 6Gbps SAS-2 RAID PCI Express HBA B4 Asic", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7069200 / 7107092", "name": "Sun 800GB PCI Express Flash Accelerator F80 SAS HBA", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "135-1205", "name": "Sun X5562A-Z 10Gbps Long Wave SFP+ Transceiver", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7046330 / 7058153", "name": "SUN X4-2 System Board Assembly", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "150-3993", "name": "Sun 3V Coin Cell Battery CR2032/BR2032", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7044130", "name": "Sun A258 1000W AC Power Supply", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7079395", "name": "Sun A256 600 Watt AC Input Power Supply", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "7013526", "name": "Sun Oracle Dual Counter Rotating Fan Module", "criticality": "Medium", "lead_time_days": 6},
        {"sku": "42D0519/46M7030", "name": '450GB 15K 3.5" SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "44W2235/44W2234", "name": '300GB 15K 3.5" SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "42C2140", "name": "AC 530 WATT POWER SUPPLY FOR EXP3000", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "39R6520/39R6519", "name": "DS3000 Memory Cache Battery", "criticality": "Medium", "lead_time_days": 6},
        {"sku": "81Y9651", "name": 'IBM 900GB 10K 6G 2.5" SFF SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "00AJ301", "name": 'IBM 600GB 15K 6G 2.5" SFF SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "653957-001", "name": 'HP 600GB 10K 6G 2.5" SFF DP SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "42D0708", "name": "IBM 500-GB 7.2K 2.5 Slim-HS SAS HDD", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "606020-001", "name": "HP 1-TB 6G 7.2K 2.5 DP SAS HDD", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "69Y2926", "name": "IBM CACHE BATTERY", "criticality": "Medium", "lead_time_days": 6},
        {"sku": "005049185", "name": "SSD 200GB LFF EFD SAS 6G VNX", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "0RVDT", "name": "Dell 300-GB 12G 15K 2.5 SAS w/G176J", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "R749K", "name": "Dell 450-GB 6G 15K 3.5 SAS w/F9541", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "V1YJ6", "name": "Dell PE 750W 80 Plus Platinum HS Power Supply", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "42D0417", "name": "IBM 300GB 15K 4GBPS HDD", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "81Y9893", "name": 'IBM 900GB 10K 6G 2.5" SFF SAS HDD', "criticality": "Critical", "lead_time_days": 4},
        {"sku": "005049185-DUP", "name": "SSD 200GB LFF EFD SAS 6G VNX (Duplicate SKU Handling)", "criticality": "Critical", "lead_time_days": 4},
        {"sku": "005049273", "name": "HDD 300GB 15K 3.5 VNX 51/5300", "criticality": "Critical", "lead_time_days": 4},
    ]

    for idx, item in enumerate(parts_data, start=1):
        sku_part = models.SkuPart(
            sku=item["sku"],
            name=item["name"],
            description=item["name"],
            criticality=item.get("criticality", "Medium"),
            rack_type=f"Rack-{random.randint(1, 10)}",
            tray_type=f"Tray-{random.randint(1, 4)}",
            platform=None,
            annual_demand=random.randint(10, 200),
        )
        db.add(sku_part)
        db.flush()

        item_count = random.randint(0, 20)
        for j in range(1, item_count + 1):
            serial = f"SN-{idx:04d}-{j:03d}"
            unit_cost = round(random.uniform(50.0, 500.0), 2)
            shipping_cost = round(random.uniform(10.0, 50.0), 2)
            holding_cost_percentage = 0.20
            annual_demand = sku_part.annual_demand
            name_lower = item["name"].lower()
            if "sun" in name_lower:
                vendor = "Sun"
            elif "ibm" in name_lower:
                vendor = "IBM"
            elif "hp" in name_lower:
                vendor = "HP"
            elif "dell" in name_lower:
                vendor = "Dell"
            elif "vnx" in name_lower or "emc" in name_lower:
                vendor = "EMC"
            else:
                vendor = "Generic Vendor"
            part_item = models.PartItem(
                sku_id=sku_part.id,
                serial_no=serial,
                outgoing_flag=False,
                returning_flag=False,
                description=sku_part.name,
                price_usd=unit_cost,
                conversion_rate=default_conversion_rate,
                price_myr=round(unit_cost * default_conversion_rate, 2),
                vendor=vendor,
                customer=None,
                engineer=None,
                remarks=None,
                lead_time_days=item.get("lead_time_days", 7),
                unit_cost=unit_cost,
                holding_cost_percentage=holding_cost_percentage,
                annual_demand=annual_demand,
                shipping_cost=shipping_cost,
                eoq_min_threshold=0,
            )
            db.add(part_item)

        print(f"Added SKU {item['sku']} with {item_count} stock items")

    db.commit()
    print("Database seeded successfully!")
    db.close()

if __name__ == "__main__":
    seed_data()
