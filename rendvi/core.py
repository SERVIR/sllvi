import ee
import pandas as pd
from rendvi.masking import Masking


class Utils:
    # helper function to add NDVI band to image collection
    @staticmethod
    def addNDBand(coll, b1=None, b2=None, outName=None):
        def _addND(image):
            nd = image.expression('(b1-b2)/(b1+b2+1e-7)',{
                'b1':image.select(b1),
                'b2':image.select(b2)
            })
            if outName is not None:
                nd = nd.rename([outName])
            return image.addBands(nd)
        return coll.map(_addND)

    # Function to lag dekad EE date array
    @staticmethod
    def lagDates(dateArray):
        dim = dateArray.length().getInfo()[0]
        first = dateArray.slice(start=0, end=dim - 1)
        last = dateArray.slice(start=1, end=dim)
        return ee.Array.cat([first, last], 1).toList()

    # Function to rescale NDVI values from 0-200 to -1-1
    @staticmethod
    def rescaleEModis(img):
        ndvi = img.expression("(ndvi - 100) / 100", {
            "ndvi": img.select("ndvi")
        }).rename('ndvi')
        inRange = ndvi.gte(-1).And(ndvi.lte(1))
        return ndvi.updateMask(inRange).set('system:time_start', img.date().millis())

    # Function to rescale NDVI values from -1-1to 0-200
    @staticmethod
    def scaleNdvi(img):
        date = ee.Date(img.get('system:time_start'))
        out = img.select(['ndvi']).add(1).multiply(100).uint8()\
            .rename(['ndvi']).set('system:time_start', img.date().millis())

        return ee.Image(out)

    @staticmethod
    def timeBand(d):
        return ee.Image(d.millis().divide(1e18)).float().rename('t')

    @staticmethod
    def addTimeBand(img):
        d = img.date()
        return img.addBands(Utils.timeBand(d))

    @staticmethod
    def addConstantBand(img):
        constBand = ee.Image(1)
        return img.addBands(constBand)

    @property
    def perpetualDekads():
        # create lists of doy values that correspond to the dekad begin dates
        perpetualDates = ee.Array([1, 11, 21, 32, 42, 52, 60, 70, 80, 91, 101, 111, 121, 131, 141, 152, 162,
                                   172, 182, 192, 202, 213, 223, 233, 244, 254, 264, 274, 284, 294, 305, 315, 325, 335, 345, 355, 366])
        # get EE Array of time periods for dekad calculations
        pDekads = ee.List(Utils.lagDates(perpetualDates))  # perpetual years

        return pDekads

    @property
    def leapYearDekads():
        leapYearDates = ee.Array([1, 11, 21, 32, 42, 52, 61, 71, 81, 92, 102, 112, 122, 132, 142, 153, 163,
                                  173, 183, 193, 203, 214, 224, 234, 245, 255, 265, 275, 285, 295, 306, 316, 326, 336, 346, 356, 367])
        lDekads = ee.List(Utils.lagDates(leapYearDates))  # leap years
        return lDekads

    @staticmethod
    def validImage(img):
        nBands = ee.Number(img.bandNames().length())
        out = ee.Algorithms.If(nBands.gt(0), img.copyProperties(img, ['system:time_start']), None)
        return out

    @staticmethod
    def validQaImage(img):
        nBands = ee.Number(img.bandNames().length())
        out = ee.Algorithms.If(nBands.gt(2), img.copyProperties(img, ['system:time_start']), None)
        return out

    @staticmethod
    def reduceQaToImages(coll, qaBand=None, renameBands=None):
        if qaBand:
            coll = coll.select(qaBand)

        countImg = coll.map(lambda img: img.unmask(1)).count()

        expanded = coll.map(Masking.qaFlagsToBands)

        qaPct = expanded.reduce(ee.Reducer.count()).divide(countImg)

        if renameBands:
            qaPct = qaPct.rename(renameBands)

        pctClear = ee.Image(ee.Algorithms.If(qaPct.bandNames().size().gt(0),
                                             ee.Image(1).subtract(
                                                 qaPct.reduce(ee.Reducer.sum())),
                                             ee.Image(0)))\
            .rename("pctClear")

        return qaPct.addBands(pctClear)


class Rendvi:
    def __init__(self, ic, band=None, seed=0):
        self.IC = ic

        if band is None:
            bandList = ee.Image(ic.first()).bandNames().getInfo()
            self.BAND = bandList[0]
        else:
            self.BAND = band

        self.SEED = seed
        return

    def __repr__(self):
        return 'Processing class for EE ImageCollections'

    @property
    def imageCollection(self):
        return self.IC

    @property
    def dates(self):
        return self.getDates().map(lambda x: ee.Date(x).format("YYYY-MM-dd")).getInfo()

    def getDates(self):
        return ee.List(self.IC.aggregate_array('system:time_start'))

    def getDekadImages(self, includeQa=True):
        # loop functions to calculate dekads from daily data
        def yrLoop(yr):
            # loop over each dekad within year
            def dkLoop(dk):
                idxArr = ee.Array(dk)  # convert list to array
                t1 = idxArr.get([0])  # get start of dekad
                t2 = idxArr.get([1])  # end of dekad
                date = y1.advance(ee.Number(t1).subtract(1), 'day')

                dekadModis = yrModis.filter(ee.Filter.calendarRange(t1, t2, 'day_of_year'))\

                composite = dekadModis.select(self.BAND).qualityMosaic(self.BAND)\
                    .rename(self.BAND).set('system:time_start', date.millis(), 'begin', ee.Number(t1))

                if includeQa:
                    nClearObs = dekadModis.select(self.BAND).count()\
                        .rename("nClearObs")

                    qaComposite = Utils.reduceQaToImages(dekadModis, qaBand="qa",
                                                         renameBands=["pctOutOfRange", "pctPoorQuality", "pctClouds", "pctShadows", "pctSnow", "pctSensorZ", "pctSolarZ"])

                    result = ee.Algorithms.If(composite,
                                              composite.addBands(
                                                  qaComposite).addBands(nClearObs),
                                              None)
                else:
                    result = ee.Algorithms.If(composite,
                                              composite,
                                              None)
                return result

            # create date objects to filter
            y1 = ee.Date.fromYMD(yr, 1, 1)
            y2 = y1.advance(1, 'year')

            yrModis = self.IC.filterDate(y1, y2)

            # get proper dekad timing based on leap year or perpetual year
            thisDekad = ee.List(ee.Algorithms.If(ee.Number(yr).mod(4).eq(0),
                                                 Utils.leapYearDekads.fget(),
                                                 Utils.perpetualDekads.fget()))

            return thisDekad.map(dkLoop)

        years = self.getDates().map(lambda x: ee.Date(x).get('year')).distinct()

        x = ee.ImageCollection(years.map(yrLoop).flatten())
        # dekads = ee.ImageCollection.fromImages(
        #     x.map(Utils.validImage, True).toList(x.size()))

        if includeQa:
            dekads = x.map(Utils.validQaImage,True)
        else:
            dekads = x.map(Utils.validImage,True)

        dekadIc = ee.ImageCollection.fromImages(dekads.toList(dekads.size()))

        return Rendvi(dekadIc, self.BAND, self.SEED)

    def calcClimatology(self):
        def _dekadsToClimo(i):
            i = ee.Number(ee.List(i).get(0))
            dummyT = ee.Date.fromYMD(2001, 1, 1)
            climoColl = self.IC.select(self.BAND).filter(
                ee.Filter.dayOfYear(i, i.add(5)))
            count = climoColl.count().rename('count').multiply(1000).uint16()
            climo = climoColl.reduce(reducers).multiply(
                10000).int16()  # .updateMask(count.gt(7));
            properties = {'system:time_start': dummyT.advance(i.subtract(1), 'day').millis(),
                          'dekad': i}
            return climo.addBands(count).set(properties)

        # Combine the mean and standard deviation reducers.
        reducers = ee.Reducer.mean().combine(
            reducer2=ee.Reducer.stdDev(), sharedInputs=True)
        dekadJDates = Utils.perpetualDekads.fget()

        dekadClimo = ee.ImageCollection(dekadJDates.map(_dekadsToClimo))

        return dekadClimo

    def applyDespike(self, window=30, step=10, offset=1, diffThresh=0.2, timeUnits="day",keepBandPattern="^(pct|nClear).*"):
        def _despike(d):
            d = ee.Date(d)

            random = ee.Image.random(self.SEED).subtract(
                0.5).multiply(2).rename(self.BAND)

            # var b
            tempFore = ee.ImageCollection(self.IC.filterDate(
                d.advance(-(window - offset), timeUnits), d.advance(-(step - offset), timeUnits)))
            tempAft = ee.ImageCollection(self.IC.filterDate(
                d.advance((step + offset), timeUnits), d.advance((window + offset), timeUnits)))

            tempMax = ee.ImageCollection(tempFore.merge(tempAft)).max()
            tempMax = ee.Image(ee.Algorithms.If(
                tempMax.bandNames().length().gt(0), tempMax, random))

            # var c
            tm1 = ee.Image(self.IC.filterDate(
                d.advance(-(step - offset), timeUnits), d.advance(-offset, timeUnits)).first())
            # var a
            t = ee.Image(self.IC.filterDate(
                d.advance(-offset, timeUnits), d.advance(offset, timeUnits)).first())
            # var d
            tp1 = ee.Image(self.IC.filterDate(
                d.advance(offset, timeUnits), d.advance((step + offset), timeUnits)).first())

            tm1 = ee.Image(ee.Algorithms.If(tm1, tm1, random))
            t = ee.Image(ee.Algorithms.If(t, t, random))
            tp1 = ee.Image(ee.Algorithms.If(tp1, tp1, random))

            # var ac
            mDiff = t.subtract(tm1).divide(tm1).abs() # percent difference t-1 to t0
            # var ad
            pDiff = tp1.subtract(t).divide(t).abs() # percent difference t0 to t1
            # var Bn
            Bn = tempMax.multiply(1.1)

            iniMask = pDiff.lte(diffThresh).Or(mDiff.lte(diffThresh))
            despikeMask = t.select(self.BAND).lt(Bn.select(self.BAND)).rename("despiked")

            maskedOut = t.select(self.BAND).updateMask(despikeMask)

            time = Utils.timeBand(d)

            out = ee.Image.cat([
                maskedOut,
                time,
                despikeMask.Not().unmask(0),
            ])
            
            if keepBandPattern is not None:
                out = out.addBands(t.select(keepBandPattern))

            return out

        dates = self.getDates()

        include = window // step

        despiked = ee.ImageCollection(
            dates.slice(include, -include).map(_despike))

        return Rendvi(despiked, self.BAND, self.SEED)

    def climatologyBackFill(self, climatology, nPeriods=5, step=10, keepBandPattern="^(pct|nClear).*"):
        def findClimoDate(img):
            t = ee.Date(img.get('system:time_start'))
            yr = ee.Number(t.get('year'))
            foy = ee.Date.fromYMD(yr, 1, 1)
            doy = t.difference(foy, 'day').int()
            climoDate = ee.Date.fromYMD(2001, 1, 1).advance(doy, 'day')
            climoN = ee.Image(climatology.filterDate(
                climoDate.advance(-1, 'day'), climoDate.advance(1, 'day')).first())
            return climoN

        def getPrevious(img):
            climo = findClimoDate(img)
            climo = climo.multiply(0.0001).updateMask(climo.select("count").gt(0.6))

            z = img.select(self.BAND).subtract(climo.select('.*mean'))\
                .divide(climo.select('.*stdDev'))
            return img.select(self.BAND).addBands(z.rename('zScore'))

        def _backFill(img):
            t = ee.Date(img.get('system:time_start'))

            climo = findClimoDate(img).multiply(0.0001).float()

            previous = self.IC.filterDate(
                t.advance(nDays, 'day'), t.advance(-1, 'day')).map(getPrevious).mean()

            nBands = ee.Number(previous.bandNames().length())
            dummy = ee.Image(0).rename('zScore').addBands(
                ee.Image(0).rename(self.BAND))
            zScore = ee.Image(ee.Algorithms.If(nBands.eq(2), previous, dummy))

            fillVal = climo.select('.*mean').add(zScore.select('zScore').multiply(climo.select('.*stdDev')))\
                .rename(self.BAND)

            keepBands = img.select(keepBandPattern)
            fillMask = img.select(self.BAND).mask().Not().And(fillVal.mask()).unmask(0).rename("climatologyFilled")

            out = ee.Image.cat([
                img.select(self.BAND).unmask(fillVal),
                keepBands,
                fillMask
            ])

            return out

        nDays = ((nPeriods * step) + 5) * -1
        filledDekads = self.IC.map(_backFill).map(Utils.addTimeBand)

        return Rendvi(filledDekads, self.BAND, self.SEED)

    def spatialSmoothing(self,kernel,zThreshold=1,constraintBand='^clima.*',keepBandPattern="^(pct|nClear).*"):
        def _smooth(image):
            valueImage = image.select(self.BAND)
            reduced = valueImage.reduceNeighborhood(reducers,kernel)
            outside = valueImage.subtract(reduced.select('.*(mean)$')).divide(reduced.select('.*(stdDev)$')).abs()
            toFill = outside.lt(zThreshold).Or(image.select(constraintBand).Not()).rename('spatialSmoothed')
            masked = valueImage.updateMask(toFill)
            smoothed = masked.unmask(reduced.select('.*(mean)$'))
            out = ee.Image.cat([smoothed,image.select(keepBandPattern)],toFill.Not())
            return out

        reducers = ee.Reducer.mean().combine(ee.Reducer.stdDev(),'',True)
        smoothedColl = self.IC.map(_smooth)

        return Rendvi(smoothedColl, self.BAND, self.SEED)

    def applySmoothing(self, window=30, step=10, maxStack=6, offset=1, timeUnits="day",keepBandPattern="^(pct|nClear).*"):
        # Function to smooth the despiked dekad time series
        def _smooth(d):
            def applyFit(img):
                return img.select('t').multiply(fit.select('scale')).add(fit.select('offset'))\
                    .set('system:time_start', img.date().millis()).rename(self.BAND)

            d = ee.Date(d)

            windowIc = self.IC.filterDate(
                d.advance(-(windowRange - offset), timeUnits), d.advance((windowRange + offset), timeUnits))

            fit = windowIc.select(['t', self.BAND])\
                .reduce(ee.Reducer.linearFit())

            out = windowIc.map(applyFit).toList(maxStack)

            return out

        def _reduceFits(d):
            d = ee.Date(d)
            tImg = ee.Image(self.IC.filterDate(d.advance(-step//2, timeUnits), d.advance(step//2, timeUnits)).first())
            keepBands = tImg.select(keepBandPattern)

            reducedLine = fitted.filterDate(d.advance(-offset, timeUnits), d.advance(offset, timeUnits))\
                .median().set('system:time_start', d.millis()).rename(self.BAND)

            smoothedMask = tImg.select(self.BAND).mask().Not().And(reducedLine.mask()).unmask(0).rename("temporalFilled")

            out = ee.Image.cat([
                reducedLine,
                keepBands,
                smoothedMask
            ])
            return out

        windowRange = window // 2
        include = window // step
        dates = self.getDates()

        windowFits = dates.slice(include, -include).map(_smooth)
        fitted = ee.ImageCollection(windowFits.flatten())

        smoothed = ee.ImageCollection(
            dates.slice(include, -include).map(_reduceFits))

        return Rendvi(smoothed, self.BAND, self.SEED)


    def getTimeSeries(self,region,scale,start=False,end=False):
        result = self.imageCollection.getRegion(region,scale).getInfo()
        df = pd.DataFrame(result[1:])
        df.columns = result[0]
        df["date"]= pd.to_datetime([t['value']*1e6 if type(t)==dict else t*1e6 for t in df["time"]] )
        df.index = df.date
        return df
