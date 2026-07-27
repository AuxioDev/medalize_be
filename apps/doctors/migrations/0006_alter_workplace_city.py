from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('doctors', '0005_backfill_workplace_city_keys'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workplace',
            name='city',
            field=models.CharField(choices=[('baku', 'Baku'), ('absheron', 'Absheron'), ('khizi', 'Khizi'), ('sumgait', 'Sumgait'), ('dashkasan', 'Dashkasan'), ('goranboy', 'Goranboy'), ('goygol', 'Goygol'), ('samukh', 'Samukh'), ('ganja', 'Ganja'), ('naftalan', 'Naftalan'), ('balakan', 'Balakan'), ('zaqatala', 'Zaqatala'), ('qakh', 'Qakh'), ('oghuz', 'Oghuz'), ('qabala', 'Qabala'), ('shaki', 'Shaki'), ('astara', 'Astara'), ('jalilabad', 'Jalilabad'), ('lerik', 'Lerik'), ('masally', 'Masally'), ('yardimli', 'Yardimli'), ('lankaran', 'Lankaran'), ('shabran', 'Shabran'), ('khachmaz', 'Khachmaz'), ('quba', 'Quba'), ('qusar', 'Qusar'), ('siyazan', 'Siyazan'), ('agdash', 'Agdash'), ('goychay', 'Goychay'), ('kurdamir', 'Kurdamir'), ('ujar', 'Ujar'), ('zardab', 'Zardab'), ('yevlakh', 'Yevlakh'), ('mingachevir', 'Mingachevir'), ('aghjabadi', 'Aghjabadi'), ('aghdam', 'Aghdam'), ('aghdara', 'Aghdara'), ('barda', 'Barda'), ('fuzuli', 'Fuzuli'), ('khojaly', 'Khojaly'), ('khojavend', 'Khojavend'), ('shusha', 'Shusha'), ('tartar', 'Tartar'), ('khankendi', 'Khankendi'), ('kalbajar', 'Kalbajar'), ('lachin', 'Lachin'), ('qubadli', 'Qubadli'), ('zangilan', 'Zangilan'), ('jabrayil', 'Jabrayil'), ('agsu', 'Agsu'), ('ismayilli', 'Ismayilli'), ('gobustan', 'Gobustan'), ('shamakhi', 'Shamakhi'), ('babek', 'Babek'), ('julfa', 'Julfa'), ('kangarli', 'Kangarli'), ('ordubad', 'Ordubad'), ('sadarak', 'Sadarak'), ('shahbuz', 'Shahbuz'), ('sharur', 'Sharur'), ('nakhchivan_city', 'Nakhchivan'), ('aghstafa', 'Aghstafa'), ('gadabay', 'Gadabay'), ('qazakh', 'Qazakh'), ('shamkir', 'Shamkir'), ('tovuz', 'Tovuz'), ('beylagan', 'Beylagan'), ('imishli', 'Imishli'), ('saatly', 'Saatly'), ('sabirabad', 'Sabirabad'), ('bilasuvar', 'Bilasuvar'), ('hajigabul', 'Hajigabul'), ('neftchala', 'Neftchala'), ('salyan', 'Salyan'), ('shirvan', 'Shirvan')], db_index=True, max_length=64),
        ),
    ]
