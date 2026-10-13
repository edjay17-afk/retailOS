const test=require('node:test');
const assert=require('node:assert/strict');
const {decimal,add,multiply,format}=require('../app/web/static/documents.js');
test('preserves full costs before multiplying and adding',()=>{
 assert.equal(format(multiply(decimal('67.321428'),decimal('12'))),'807.857136');
 assert.equal(format(add(multiply(decimal('67.321428'),decimal('12')),multiply(decimal('0.123456789123456789'),decimal('2.5')))),'808.1657779728086419725');
});
test('keeps more than 28 significant digits and subtracts discounts exactly',()=>{
 assert.equal(format(multiply(decimal('67.321428123456789123456789123456789'),decimal('12'))),'807.857137481481469481481469481481468');
 assert.equal(format(add(decimal('1'),decimal('1.12'),true)),'-0.12');
 assert.throws(()=>decimal('NaN'));assert.throws(()=>decimal('-1'));assert.throws(()=>decimal('1e3'));
});
